"""Software autofocus via contrast detection.

For cameras that expose ``focus_absolute`` but no standard V4L2 AF toggle,
sweep the focus range and pick the position with the highest Laplacian
variance over the center ROI.

Design choices addressing observed quality problems:

1. **Multi-sample median per position.** Single-frame sharpness is noisy
   (motion blur on settle edges, AE wobble, JPEG/sensor noise). We take
   ``samples`` frames at each focus value and take the median — robust to
   one bad reading and free of overshoot from any single frame.

2. **Frame-seq settle synchronization.** After ``set_focus()`` we wait for
   the motor to settle (``settle_s``), then explicitly wait for *new*
   frames from the capture thread (via ``FrameSource.latest_seq``) before
   measuring. This prevents using a frame captured during motor motion.

3. **AE lock.** AF iterations that touch focus also push the camera ISP's
   auto-exposure controller (since the image content changes), and AE drift
   *itself* affects Laplacian variance independent of true sharpness. If
   the camera exposes ``auto_exposure``, we switch to Manual for the
   duration and restore on exit.

4. **Uni-directional sweep within each stage.** Mechanical focus motors
   have backlash (~1–3 units). Going strictly min→max within a stage
   means same-direction movement so positional accuracy is consistent.

5. **Three-stage sweep + edge handling.** Coarse(16) → Fine(8) around the
   coarse peak → small ``±_HILL_RADIUS`` hill-climb. If the coarse peak is
   at the boundary, the fine window is shifted inward to maintain its
   width (handles "peak past the edge" cases).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import cv2
import numpy as np

from app.camera.backends.v4l2 import _run_v4l2_ctl
from app.camera.controller import CameraController
from app.camera.errors import CameraNotFound
from app.camera.frame_source import FrameSource
from app.camera.models import FocusRange
from app.camera.registry import CameraRegistry
from app.stream.coordinator import StreamCoordinator


_log = logging.getLogger(__name__)

_ROI_FRACTION = 0.5  # half each side → 1/4 area at center

# Stage 1 — coarse sweep across the full range
_COARSE_STEPS = 16
_COARSE_SAMPLES = 3
_COARSE_SETTLE_S = 0.20

# Stage 2 — fine sweep around the coarse peak (±1 coarse step)
_FINE_STEPS = 8
_FINE_SAMPLES = 3
_FINE_SETTLE_S = 0.15

# Stage 3 — hill climb within ±_HILL_RADIUS of the running best
_HILL_RADIUS = 2
_HILL_SAMPLES = 3
_HILL_SETTLE_S = 0.10

_FRAME_TIMEOUT_S = 1.5  # per-measurement upper bound

_AE_CTRL_VALUE_RE = re.compile(r"auto_exposure:\s*(-?\d+)")


class AutofocusError(Exception):
    """Software AF could not run (no manual focus / no active stream / no frames)."""


@dataclass(frozen=True, slots=True)
class AutofocusResult:
    best_focus: int
    best_sharpness: float
    elapsed_ms: int
    attempts: int


def measure_sharpness(frame_bgr: np.ndarray) -> float:
    """Variance of Laplacian on the center ROI. Higher == sharper."""
    h, w = frame_bgr.shape[:2]
    rh = max(1, int(h * _ROI_FRACTION))
    rw = max(1, int(w * _ROI_FRACTION))
    y0 = (h - rh) // 2
    x0 = (w - rw) // 2
    roi = frame_bgr[y0:y0 + rh, x0:x0 + rw]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class SoftwareAutofocus:
    def __init__(
        self,
        registry: CameraRegistry,
        coordinator: StreamCoordinator,
    ) -> None:
        self._registry = registry
        self._coord = coordinator
        self._lock = asyncio.Lock()

    async def run(self, camera_id: str) -> AutofocusResult:
        async with self._lock:
            camera = self._registry.get(camera_id)
            if camera is None:
                raise CameraNotFound(camera_id)
            if not camera.capabilities.has_manual_focus or camera.capabilities.focus is None:
                raise AutofocusError("camera has no manual focus control")

            source = self._coord.get_active_source_for(camera_id)
            if source is None:
                raise AutofocusError("camera is not currently streaming")

            controller = source.controller
            focus_range = camera.capabilities.focus
            loop = asyncio.get_event_loop()
            start_t = loop.time()
            attempts = 0

            prev_ae_mode = self._ae_lock(camera.device_path)
            if prev_ae_mode is not None and prev_ae_mode != 1:
                _log.info("AF: AE locked (was mode=%d)", prev_ae_mode)

            try:
                # --- Stage 1: coarse sweep min → max -----------------------------
                coarse_results, n = await self._sweep(
                    source, controller,
                    focus_range.min, focus_range.max, _COARSE_STEPS,
                    settle_s=_COARSE_SETTLE_S, samples=_COARSE_SAMPLES,
                )
                attempts += n
                best_c_idx = max(range(len(coarse_results)), key=lambda i: coarse_results[i][1])
                best_focus, best_score = coarse_results[best_c_idx]
                _log.info(
                    "AF coarse: best focus=%d score=%.2f (idx=%d/%d)",
                    best_focus, best_score, best_c_idx, len(coarse_results) - 1,
                )

                # --- Stage 2: fine sweep around the coarse peak ------------------
                coarse_step = (focus_range.max - focus_range.min) / max(_COARSE_STEPS - 1, 1)
                fine_lo, fine_hi = self._fine_window(
                    best_focus, coarse_step, focus_range, best_c_idx, _COARSE_STEPS,
                )
                if fine_hi > fine_lo:
                    fine_results, n = await self._sweep(
                        source, controller,
                        fine_lo, fine_hi, _FINE_STEPS,
                        settle_s=_FINE_SETTLE_S, samples=_FINE_SAMPLES,
                    )
                    attempts += n
                    best_f_idx = max(range(len(fine_results)), key=lambda i: fine_results[i][1])
                    if fine_results[best_f_idx][1] > best_score:
                        best_focus, best_score = fine_results[best_f_idx]
                    _log.info("AF fine: best focus=%d score=%.2f", best_focus, best_score)

                # --- Stage 3: small hill climb around the running best -----------
                for delta in range(-_HILL_RADIUS, _HILL_RADIUS + 1):
                    if delta == 0:
                        continue
                    v = best_focus + delta
                    if v < focus_range.min or v > focus_range.max:
                        continue
                    score = await self._measure(
                        source, controller, v,
                        settle_s=_HILL_SETTLE_S, samples=_HILL_SAMPLES,
                    )
                    attempts += 1
                    if score > best_score:
                        best_focus = v
                        best_score = score
                _log.info("AF hill: best focus=%d score=%.2f", best_focus, best_score)

                # Apply final value
                controller.set_focus(best_focus)
            finally:
                self._ae_unlock(camera.device_path, prev_ae_mode)

            elapsed_ms = int((loop.time() - start_t) * 1000)
            _log.info(
                "AF complete: id=%s focus=%d sharpness=%.2f elapsed=%dms attempts=%d",
                camera_id, best_focus, best_score, elapsed_ms, attempts,
            )
            return AutofocusResult(
                best_focus=best_focus,
                best_sharpness=best_score,
                elapsed_ms=elapsed_ms,
                attempts=attempts,
            )

    # ------------------------------------------------------------------------

    @staticmethod
    def _fine_window(
        center: int, coarse_step: float, focus_range: FocusRange,
        best_idx: int, coarse_total: int,
    ) -> tuple[int, int]:
        """Compute the fine sweep window around ``center``.

        At the coarse boundary, shift the window inward so its width stays
        ~``2 * coarse_step`` (peak might be just past the edge sample).
        """
        span = coarse_step
        lo = center - span
        hi = center + span
        if best_idx == 0:
            hi = center + 2 * span
        elif best_idx == coarse_total - 1:
            lo = center - 2 * span
        return (
            max(focus_range.min, int(round(lo))),
            min(focus_range.max, int(round(hi))),
        )

    async def _sweep(
        self,
        source: FrameSource,
        controller: CameraController,
        lo: int, hi: int, steps: int,
        settle_s: float, samples: int,
    ) -> tuple[list[tuple[int, float]], int]:
        """Uni-directional sweep ``lo → hi``. Returns (positions+scores, count)."""
        if steps <= 1 or hi <= lo:
            score = await self._measure(source, controller, lo, settle_s, samples)
            return [(lo, score)], 1
        values: list[int] = []
        seen = set()
        for i in range(steps):
            v = int(round(lo + (hi - lo) * i / (steps - 1)))
            if v in seen:
                continue
            seen.add(v)
            values.append(v)
        results: list[tuple[int, float]] = []
        for v in values:
            score = await self._measure(source, controller, v, settle_s, samples)
            results.append((v, score))
            _log.debug("AF sweep: focus=%d score=%.2f", v, score)
        return results, len(values)

    async def _measure(
        self,
        source: FrameSource,
        controller: CameraController,
        focus: int,
        settle_s: float,
        samples: int,
    ) -> float:
        """Set focus, wait for motor settle, sample ``samples`` *new* frames, return median sharpness."""
        controller.set_focus(focus)
        await asyncio.sleep(settle_s)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + _FRAME_TIMEOUT_S
        last_seq = source.latest_seq
        scores: list[float] = []
        while len(scores) < samples and loop.time() < deadline:
            current_seq = source.latest_seq
            if current_seq > last_seq:
                frame = source.get_latest_frame()
                if frame is not None:
                    scores.append(measure_sharpness(frame))
                    last_seq = current_seq
                    continue
            await asyncio.sleep(0.005)
        if not scores:
            return 0.0
        scores.sort()
        return scores[len(scores) // 2]

    # --- AE lock (best-effort) ----------------------------------------------

    @staticmethod
    def _ae_lock(device_path: str) -> int | None:
        """Switch ``auto_exposure`` to Manual(1). Returns prior mode or None."""
        out = _run_v4l2_ctl(["--device", device_path, "--get-ctrl", "auto_exposure"])
        if not out:
            return None
        m = _AE_CTRL_VALUE_RE.search(out)
        if not m:
            return None
        prev = int(m.group(1))
        if prev == 1:
            return prev
        ok = _run_v4l2_ctl(["--device", device_path, "--set-ctrl", "auto_exposure=1"])
        if ok is None:
            return None
        return prev

    @staticmethod
    def _ae_unlock(device_path: str, prev_mode: int | None) -> None:
        if prev_mode is None or prev_mode == 1:
            return
        _run_v4l2_ctl(
            ["--device", device_path, "--set-ctrl", f"auto_exposure={prev_mode}"]
        )
        _log.info("AF: AE restored to mode=%d", prev_mode)
