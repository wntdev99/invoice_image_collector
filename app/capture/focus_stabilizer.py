"""AF stabilization wait.

Polls V4L2 focus_absolute at a fixed rate and considers the focus
"stable" once a sliding window of recent readings spans less than a
threshold (a fraction of the focus range).

In this codebase no currently-connected camera exposes a standard V4L2
AF toggle (``has_autofocus=False`` on both Arducam B0478 and the Chicony
webcam), so the public ``wait_stable`` is only invoked when capability
allows. The class is kept generic so a future AF-capable camera works
without changes here.
"""
from __future__ import annotations

import logging
import time
from collections import deque

import cv2

from app.camera.models import FocusRange


_log = logging.getLogger(__name__)


class FocusStabilizer:
    def __init__(
        self,
        window: int = 10,
        threshold_pct: float = 0.5,
        max_wait_s: float = 5.0,
        poll_hz: float = 10.0,
    ) -> None:
        self._window = window
        self._threshold_pct = threshold_pct
        self._max_wait = max_wait_s
        self._poll_interval = 1.0 / poll_hz

    def wait_stable(self, cap: cv2.VideoCapture, focus_range: FocusRange) -> bool:
        span = focus_range.max - focus_range.min
        threshold = max(1, int(span * self._threshold_pct / 100))
        samples: deque[int] = deque(maxlen=self._window)
        start = time.monotonic()

        while time.monotonic() - start < self._max_wait:
            ok, _ = cap.read()  # consume a frame to keep V4L2 buffer rotating
            if not ok:
                time.sleep(self._poll_interval)
                continue
            value = int(cap.get(cv2.CAP_PROP_FOCUS))
            samples.append(value)
            if len(samples) >= self._window:
                spread = max(samples) - min(samples)
                if spread <= threshold:
                    return True
            time.sleep(self._poll_interval)
        _log.warning("AF stabilization timed out after %.1fs", self._max_wait)
        return False
