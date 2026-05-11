"""V4L2-specific probing and capture utilities.

Capability probing uses subprocess to ``v4l2-ctl`` (best-effort).
Capture uses OpenCV's V4L2 backend.
"""
from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

import cv2

from app.camera.errors import CameraBusy
from app.camera.models import Capabilities

if TYPE_CHECKING:
    import numpy as np


_log = logging.getLogger(__name__)

_AF_CTRL_TOKENS = ("focus_automatic_continuous", "focus_auto")
_MF_CTRL_TOKENS = ("focus_absolute", "focus_relative")


# ---------------------------------------------------------------------------
# Capability probing (subprocess to v4l2-ctl)
# ---------------------------------------------------------------------------


def probe_capabilities(device_path: str) -> Capabilities:
    ctrls_text = _run_v4l2_ctl(["--device", device_path, "--list-ctrls"]) or ""
    formats, resolutions = _probe_formats_and_resolutions(device_path)
    return Capabilities(
        has_autofocus=any(t in ctrls_text for t in _AF_CTRL_TOKENS),
        has_manual_focus=any(t in ctrls_text for t in _MF_CTRL_TOKENS),
        formats=tuple(formats),
        resolutions=tuple(resolutions),
    )


def _run_v4l2_ctl(args: list[str], timeout: float = 2.0) -> str | None:
    try:
        result = subprocess.run(
            ["v4l2-ctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        _log.warning("v4l2-ctl not installed; install 'v4l-utils' for capability probing")
        return None
    except subprocess.TimeoutExpired:
        _log.warning("v4l2-ctl %s timed out", args)
        return None
    except OSError as exc:
        _log.warning("v4l2-ctl %s failed: %s", args, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _probe_formats_and_resolutions(
    device_path: str,
) -> tuple[list[str], list[tuple[int, int]]]:
    out = _run_v4l2_ctl(["--device", device_path, "--list-formats-ext"])
    if out is None:
        return [], []

    formats: list[str] = []
    resolutions: set[tuple[int, int]] = set()
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("[") and "]:" in s and "'" in s:
            try:
                formats.append(s.split("'", 2)[1])
            except IndexError:
                pass
        elif s.startswith("Size:"):
            for token in s.split():
                if "x" in token:
                    parts = token.split("x")
                    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                        resolutions.add((int(parts[0]), int(parts[1])))
                        break
    return formats, sorted(resolutions, key=lambda wh: (wh[0] * wh[1], wh[0]))


# ---------------------------------------------------------------------------
# Helpers for preview resolution / format selection
# ---------------------------------------------------------------------------


def best_match_resolution(
    available: tuple[tuple[int, int], ...],
    target: tuple[int, int] = (1280, 720),
) -> tuple[int, int]:
    if not available:
        return target
    if target in available:
        return target
    tw, th = target
    return min(available, key=lambda r: (r[0] - tw) ** 2 + (r[1] - th) ** 2)


def preferred_format(formats: tuple[str, ...]) -> str | None:
    """Pick a stream-friendly fourcc. MJPG is already compressed → cheaper."""
    if "MJPG" in formats:
        return "MJPG"
    if "YUYV" in formats:
        return "YUYV"
    return None


# ---------------------------------------------------------------------------
# Streaming capture device (OpenCV V4L2 backend)
# ---------------------------------------------------------------------------


class V4L2CaptureDevice:
    """One open V4L2 capture handle. Step 3 scope: open/read/release only."""

    def __init__(self, device_path: str) -> None:
        self._device_path = device_path
        self._cap: cv2.VideoCapture | None = None
        self._negotiated: tuple[int, int, float] = (0, 0, 0.0)

    @property
    def device_path(self) -> str:
        return self._device_path

    @property
    def negotiated(self) -> tuple[int, int, float]:
        return self._negotiated

    def open(
        self,
        width: int,
        height: int,
        fps: int,
        fourcc: str | None = None,
    ) -> tuple[int, int, float]:
        cap = cv2.VideoCapture(self._device_path, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise CameraBusy(
                f"failed to open V4L2 device {self._device_path} "
                "(in use by another process or no permission?)"
            )
        if fourcc:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

        self._cap = cap
        self._negotiated = (actual_w, actual_h, actual_fps)
        _log.info(
            "V4L2 opened: path=%s requested=%dx%d@%d negotiated=%dx%d@%.1f fourcc=%s",
            self._device_path, width, height, fps,
            actual_w, actual_h, actual_fps, fourcc or "(default)",
        )
        return self._negotiated

    def read(self) -> "tuple[bool, np.ndarray | None]":
        if self._cap is None:
            return False, None
        return self._cap.read()

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            _log.info("V4L2 released: path=%s", self._device_path)
