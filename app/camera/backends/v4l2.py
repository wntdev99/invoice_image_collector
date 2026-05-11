"""V4L2-specific probing utilities.

Capability probing is best-effort — if v4l2-ctl is missing or the device
is misbehaving, we return defaults rather than crash. The full streaming
backend (open / read_frame / close) will be added in step 3.
"""
from __future__ import annotations

import logging
import subprocess

from app.camera.models import Capabilities


_log = logging.getLogger(__name__)

_AF_CTRL_TOKENS = ("focus_automatic_continuous", "focus_auto")
_MF_CTRL_TOKENS = ("focus_absolute", "focus_relative")


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
