"""Camera hot-plug discovery via pyudev.

Runs udev monitoring in a background thread (pyudev.MonitorObserver) and
marshals events back into the asyncio loop via call_soon_threadsafe.
"""
from __future__ import annotations

import asyncio
import logging
import re

import pyudev

from app.camera.backends import v4l2 as v4l2_backend
from app.camera.backends.v4l2 import has_color_format
from app.camera.events import CameraAttached, CameraDetached
from app.camera.models import Camera
from app.camera.registry import CameraRegistry
from app.core.events import EventBus


_log = logging.getLogger(__name__)

_ID_SANITIZE = re.compile(r"[^A-Za-z0-9_-]")


def _safe_id(*parts: str | None) -> str:
    joined = "_".join(p for p in parts if p)
    return _ID_SANITIZE.sub("_", joined)


def _device_to_camera(device: pyudev.Device) -> Camera | None:
    idx_raw = device.attributes.get("index")
    if idx_raw is not None:
        try:
            if int(idx_raw) != 0:
                return None
        except (ValueError, TypeError):
            pass

    device_path = device.device_node
    if not device_path:
        return None

    vendor = device.properties.get("ID_VENDOR_ID") or ""
    product = device.properties.get("ID_MODEL_ID") or ""
    serial = device.properties.get("ID_SERIAL_SHORT")
    name = (
        device.properties.get("ID_V4L_PRODUCT")
        or device.properties.get("ID_MODEL")
        or device.properties.get("ID_VENDOR")
        or "Unknown camera"
    )
    bus_path = device.properties.get("ID_PATH")

    cam_id = _safe_id(vendor, product, serial or bus_path or device_path)

    capabilities = v4l2_backend.probe_capabilities(device_path)

    # Skip depth/IR-only nodes (Z16, Y16, GREY etc.) — multi-stream cameras
    # like Orbbec Gemini 336 expose several index=0 video nodes per physical
    # device, and only the RGB one has a colour fourcc cv2 can decode.
    if not has_color_format(capabilities.formats):
        _log.info(
            "discovery: skipping non-color node %s (formats=%s)",
            device_path, list(capabilities.formats),
        )
        return None

    return Camera(
        id=cam_id,
        device_path=device_path,
        name=name,
        vendor_id=vendor,
        product_id=product,
        serial=serial,
        bus_path=bus_path,
        capabilities=capabilities,
    )


class CameraDiscovery:
    def __init__(self, registry: CameraRegistry, bus: EventBus) -> None:
        self._registry = registry
        self._bus = bus
        self._context = pyudev.Context()
        self._monitor = pyudev.Monitor.from_netlink(self._context)
        self._monitor.filter_by(subsystem="video4linux")
        self._observer: pyudev.MonitorObserver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        for device in self._context.list_devices(subsystem="video4linux"):
            self._handle_add(device)
        self._observer = pyudev.MonitorObserver(
            self._monitor, self._on_udev_event, name="camera-udev-observer"
        )
        self._observer.start()
        _log.info("camera discovery started; %d camera(s) registered", len(self._registry.list()))

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer = None

    def _on_udev_event(self, action: str, device: pyudev.Device) -> None:
        if action == "add":
            self._handle_add(device)
        elif action == "remove":
            self._handle_remove(device)

    def _handle_add(self, device: pyudev.Device) -> None:
        camera = _device_to_camera(device)
        if camera is None:
            return
        if self._registry.add(camera):
            _log.info("camera attached: id=%s path=%s", camera.id, camera.device_path)
            self._publish(CameraAttached(camera=camera))

    def _handle_remove(self, device: pyudev.Device) -> None:
        device_path = device.device_node
        if not device_path:
            return
        cam = self._registry.find_by_device_path(device_path)
        if cam is None:
            return
        removed = self._registry.remove(cam.id)
        if removed is not None:
            _log.info("camera detached: id=%s path=%s", cam.id, device_path)
            self._publish(CameraDetached(camera_id=cam.id))

    def _publish(self, event: object) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._bus.publish, event)
