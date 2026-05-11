"""Software autofocus via contrast detection.

For cameras that expose ``focus_absolute`` but no standard V4L2 AF toggle
(e.g. Arducam B0478), we implement AF ourselves: sweep the focus range,
score each position by Laplacian variance over the central ROI, apply
the best value.

Two-stage sweep:
  1. Coarse:  12 evenly-spaced positions across the full focus range.
  2. Fine:    8 positions in ``±(coarse_step)`` around the coarse maximum.

Coarse-to-fine is robust against secondary peaks (which appear for some
lens-sensor stacks at extreme focus values) while staying under ~3 s.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from app.camera.errors import CameraNotFound
from app.camera.registry import CameraRegistry
from app.stream.coordinator import StreamCoordinator

if TYPE_CHECKING:
    pass


_log = logging.getLogger(__name__)

_SETTLE_S = 0.15        # focus motor settle delay per step
_COARSE_STEPS = 12
_FINE_STEPS = 8
_ROI_FRACTION = 0.5     # half of each side → center 1/4 of area


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

            # ----- Stage 1: coarse sweep -----
            coarse_values = [
                int(v) for v in np.linspace(focus_range.min, focus_range.max, _COARSE_STEPS)
            ]
            coarse_scores: list[float] = []
            for v in coarse_values:
                controller.set_focus(v)
                await asyncio.sleep(_SETTLE_S)
                frame = source.get_latest_frame()
                if frame is None:
                    raise AutofocusError("no frame available from active stream")
                score = measure_sharpness(frame)
                coarse_scores.append(score)
                attempts += 1
                _log.debug("AF coarse: focus=%d score=%.2f", v, score)

            best_c_idx = int(np.argmax(coarse_scores))
            best_c_value = coarse_values[best_c_idx]
            best_c_score = coarse_scores[best_c_idx]

            # ----- Stage 2: fine sweep around the coarse maximum -----
            coarse_step = (focus_range.max - focus_range.min) / max(_COARSE_STEPS - 1, 1)
            fine_lo = max(focus_range.min, int(best_c_value - coarse_step))
            fine_hi = min(focus_range.max, int(best_c_value + coarse_step))

            if fine_hi <= fine_lo:
                best_value = best_c_value
                best_score = best_c_score
            else:
                fine_values = [
                    int(v) for v in np.linspace(fine_lo, fine_hi, _FINE_STEPS)
                ]
                fine_scores: list[float] = []
                for v in fine_values:
                    controller.set_focus(v)
                    await asyncio.sleep(_SETTLE_S)
                    frame = source.get_latest_frame()
                    if frame is None:
                        raise AutofocusError("no frame available from active stream (fine)")
                    score = measure_sharpness(frame)
                    fine_scores.append(score)
                    attempts += 1
                    _log.debug("AF fine: focus=%d score=%.2f", v, score)

                best_f_idx = int(np.argmax(fine_scores))
                # Prefer fine result only if it actually beats coarse (sanity)
                if fine_scores[best_f_idx] >= best_c_score:
                    best_value = fine_values[best_f_idx]
                    best_score = fine_scores[best_f_idx]
                else:
                    best_value = best_c_value
                    best_score = best_c_score

            # Apply the final value (may already be applied by last fine probe)
            controller.set_focus(best_value)
            elapsed_ms = int((loop.time() - start_t) * 1000)
            _log.info(
                "AF complete: id=%s focus=%d sharpness=%.2f elapsed=%dms attempts=%d",
                camera_id, best_value, best_score, elapsed_ms, attempts,
            )
            return AutofocusResult(
                best_focus=best_value,
                best_sharpness=best_score,
                elapsed_ms=elapsed_ms,
                attempts=attempts,
            )
