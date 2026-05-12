"""V4L2-specific probing and capture utilities.

Capability probing uses subprocess to ``v4l2-ctl`` (best-effort).
Capture and ctrl access use OpenCV's V4L2 backend.
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import TYPE_CHECKING

import cv2

from app.camera.errors import CameraBusy
from app.camera.models import Capabilities, FocusRange, PowerLineFrequency

if TYPE_CHECKING:
    import numpy as np


_log = logging.getLogger(__name__)

_AF_CTRL_TOKENS = ("focus_automatic_continuous", "focus_auto")
_MF_CTRL_TOKENS = ("focus_absolute", "focus_relative")
_CTRL_RANGE_RE = re.compile(
    r"min=(-?\d+)\s+max=(-?\d+)(?:\s+step=(\d+))?\s+default=(-?\d+)"
)


# ---------------------------------------------------------------------------
# Capability probing (subprocess to v4l2-ctl)
# ---------------------------------------------------------------------------


def probe_capabilities(device_path: str) -> Capabilities:
    # --list-ctrls-menus (-L) also emits indented "N: Label" rows for menu
    # controls, which we need to discover power_line_frequency options.
    ctrls_text = _run_v4l2_ctl(["--device", device_path, "--list-ctrls-menus"]) or ""
    formats, resolutions = _probe_formats_and_resolutions(device_path)
    return Capabilities(
        has_autofocus=any(t in ctrls_text for t in _AF_CTRL_TOKENS),
        has_manual_focus=any(t in ctrls_text for t in _MF_CTRL_TOKENS),
        focus=_probe_focus_range(ctrls_text),
        power_line_frequency=_probe_power_line_frequency(ctrls_text),
        formats=tuple(formats),
        resolutions=tuple(resolutions),
    )


def _v4l2_set_ctrl(device_path: str, name: str, value: int) -> bool:
    out = _run_v4l2_ctl(
        ["--device", device_path, "--set-ctrl", f"{name}={value}"]
    )
    return out is not None


_CTRL_VALUE_RE = re.compile(r":\s*(-?\d+)\s*$")


def _v4l2_get_int_ctrl(device_path: str, name: str) -> int | None:
    out = _run_v4l2_ctl(
        ["--device", device_path, "--get-ctrl", name]
    )
    if not out:
        return None
    # Output looks like:  "power_line_frequency: 2"
    for line in out.splitlines():
        if name in line:
            m = _CTRL_VALUE_RE.search(line)
            if m:
                return int(m.group(1))
    return None


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


def _probe_focus_range(ctrls_text: str) -> FocusRange | None:
    for line in ctrls_text.splitlines():
        if "focus_absolute" not in line:
            continue
        m = _CTRL_RANGE_RE.search(line)
        if m is None:
            return None
        step_str = m.group(3)
        return FocusRange(
            min=int(m.group(1)),
            max=int(m.group(2)),
            step=max(int(step_str) if step_str else 1, 1),
            default=int(m.group(4)),
        )
    return None


_MENU_OPTION_RE = re.compile(r"^\s+(\d+):\s+(.+?)\s*$")


def _probe_power_line_frequency(ctrls_text: str) -> PowerLineFrequency | None:
    lines = ctrls_text.splitlines()
    for i, line in enumerate(lines):
        if "power_line_frequency" not in line or "(menu)" not in line:
            continue
        rng = _CTRL_RANGE_RE.search(line)
        if rng is None:
            return None
        # Collect indented "N: Label" rows that immediately follow.
        options: list[tuple[int, str]] = []
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                # blank line breaks the option block on some v4l2-utils versions
                if options:
                    break
                continue
            mo = _MENU_OPTION_RE.match(nxt)
            if mo is None:
                break
            options.append((int(mo.group(1)), mo.group(2).strip()))
        if not options:
            return None
        return PowerLineFrequency(
            min=int(rng.group(1)),
            max=int(rng.group(2)),
            default=int(rng.group(4)),
            options=tuple(options),
        )
    return None


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


# Whitelist of fourcc codes that represent a colour stream cv2.VideoCapture
# can decode into BGR. Depth (Z16, INVZ) and IR (Y16, GREY, INVI) sensor nodes
# expose only non-colour formats; we deliberately skip them at discovery so
# multi-stream cameras like Orbbec Gemini 336 don't get registered through
# their depth node.
_COLOR_FOURCCS = frozenset({
    "YUYV", "YUY2",
    "MJPG", "MJPEG", "JPEG",
    "NV12", "NV21",
    "RGB3", "BGR3", "RGB24", "BGR24",
    "YU12", "YV12", "I420",
    "UYVY",
})


def has_color_format(formats: tuple[str, ...]) -> bool:
    return any(f.strip().upper() in _COLOR_FOURCCS for f in formats)


# ---------------------------------------------------------------------------
# Streaming capture device (OpenCV V4L2 backend)
# ---------------------------------------------------------------------------


class V4L2CaptureDevice:
    """One open V4L2 capture handle. Exposes capture + live ctrl get/set."""

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

    @property
    def is_open(self) -> bool:
        return self._cap is not None

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

    # ----- Live V4L2 ctrl access (no-op when device is not open) -----

    def get_focus(self) -> int | None:
        if self._cap is None:
            return None
        return int(self._cap.get(cv2.CAP_PROP_FOCUS))

    def set_focus(self, value: int) -> int | None:
        if self._cap is None:
            return None
        self._cap.set(cv2.CAP_PROP_FOCUS, float(value))
        return self.get_focus()

    def get_autofocus(self) -> bool | None:
        if self._cap is None:
            return None
        v = self._cap.get(cv2.CAP_PROP_AUTOFOCUS)
        return bool(v) if v >= 0 else None

    def set_autofocus(self, enabled: bool) -> bool | None:
        if self._cap is None:
            return None
        self._cap.set(cv2.CAP_PROP_AUTOFOCUS, 1.0 if enabled else 0.0)
        return self.get_autofocus()

    # ``power_line_frequency`` has no dedicated cv2.CAP_PROP_* mapping, so we
    # go via v4l2-ctl. uvcvideo allows ctrl ioctls on a separate fd while our
    # cv2.VideoCapture is streaming, so this is safe to call mid-stream.

    def get_power_line_frequency(self) -> int | None:
        return _v4l2_get_int_ctrl(self._device_path, "power_line_frequency")

    def set_power_line_frequency(self, value: int) -> int | None:
        ok = _v4l2_set_ctrl(self._device_path, "power_line_frequency", value)
        if not ok:
            return None
        return self.get_power_line_frequency()
