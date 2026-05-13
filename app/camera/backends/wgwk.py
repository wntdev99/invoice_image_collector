"""WGWK-AS500J / MC800S5 IP camera backend.

V4L2 USB 카메라가 아닌 네트워크 IP 카메라(HAPI + SCF + RTSP)를 기존
``V4L2CaptureDevice`` 인터페이스에 맞춰 노출한다. ``cv2.VideoCapture``가
RTSP URL을 native 지원하므로 frame layer는 거의 그대로 재사용 가능하며,
focus·AF 등 control layer만 wgwk_camera 라이브러리로 brigde.

설계 결정:
  - ``device_path``는 ``wgwk://<host>`` 형식으로 합성. V4L2 ``/dev/videoN``과
    충돌하지 않고, FrameSource가 prefix로 dispatch할 수 있다.
  - Focus는 client-side 정수 카운터(0~100). 본 카메라는 motor absolute
    encoder readback이 불가하므로 (`docs/08 §8.5`) 카운터는 상대 위치만
    track. 1 unit = ``_MS_PER_FOCUS_UNIT`` ms의 ``focus_near``/``focus_far``.
  - AF는 wgwk_camera의 SCF set_af로 매핑. SCF 토큰은 env에서 로드.
  - power_line_frequency는 해당 없음 → 항상 None.

알려진 한계:
  - Focus 카운터는 anchor 없이 시작하므로 절대 위치 의미가 없음. 슬라이더는
    상대 모터 컨트롤러처럼 동작.
  - 슬라이더 빠른 drag 시 set_focus가 blocking HAPI 호출이라 약간의 latency.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2

from app.camera.errors import CameraBusy
from app.camera.events import CameraAttached
from app.camera.models import Camera, Capabilities, FocusRange, ZoomRange

if TYPE_CHECKING:
    import numpy as np

    from app.camera.registry import CameraRegistry
    from app.core.events import EventBus


_log = logging.getLogger(__name__)


# Focus 가상 범위. 0=가까이 끝, FOCUS_MAX=멀리 끝. 카운터 단위.
_FOCUS_MIN = 0
_FOCUS_MAX = 100
_FOCUS_DEFAULT = 50
_FOCUS_STEP = 1
# 한 카운터 단위당 HAPI focus 명령 시간. 50ms × 100 units = 5s 전체 sweep.
# 실측: focus 명령 1회 500ms ≈ focus 10 unit 변화 정도.
_MS_PER_FOCUS_UNIT = 50

# Optical zoom 범위 = KF 카운터 (AS500J: 1=wide, 36=tele, 광학 1x~10x).
# wgwk_camera.ZoomTracker.max_kf와 동일. 슬라이더 1 unit = 1 KF = ~185ms motor.
_ZOOM_MIN = 1
_ZOOM_MAX = 36
_ZOOM_DEFAULT = 1
_ZOOM_STEP = 1

# WGWK Camera "vendor" id (UI에 표시).
_VENDOR = "wgwk"
_PRODUCT = "as500j"


# ---------------------------------------------------------------------------
# Config registry (camera.id → credentials)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WgwkConfig:
    host: str
    name: str = "WGWK-AS500J"
    username: str = "admin"
    password: str = "123456"
    scf_userid: str | None = None
    scf_passwd: str | None = None


_configs: dict[str, WgwkConfig] = {}


def register_config(camera_id: str, config: WgwkConfig) -> None:
    """Camera.id 와 연동된 credentials/host를 모듈 내부에 등록.

    backend dispatcher가 ``device_path``에서 camera.id를 매핑할 수 있도록
    호출자(보통 main lifespan startup)가 미리 호출해야 한다.
    """
    _configs[camera_id] = config


def get_config(camera_id: str) -> WgwkConfig:
    return _configs[camera_id]


def build_camera_model(cfg: WgwkConfig, camera_id: str | None = None) -> Camera:
    """`Camera` dataclass instance를 만든다. registry.add 직전에 사용."""
    cam_id = camera_id or f"wgwk-{cfg.host.replace('.', '-')}"
    return Camera(
        id=cam_id,
        device_path=f"wgwk://{cfg.host}",
        name=cfg.name,
        vendor_id=_VENDOR,
        product_id=_PRODUCT,
        serial=None,
        bus_path=None,
        capabilities=Capabilities(
            has_autofocus=True,
            has_manual_focus=True,
            focus=FocusRange(
                min=_FOCUS_MIN, max=_FOCUS_MAX,
                step=_FOCUS_STEP, default=_FOCUS_DEFAULT,
            ),
            zoom=ZoomRange(
                min=_ZOOM_MIN, max=_ZOOM_MAX,
                step=_ZOOM_STEP, default=_ZOOM_DEFAULT,
            ),
            power_line_frequency=None,
            formats=(),       # RTSP는 카메라가 결정
            resolutions=(),
        ),
    )


# ---------------------------------------------------------------------------
# Static camera registration helper
# ---------------------------------------------------------------------------


def parse_env_cameras() -> list[WgwkConfig]:
    """환경변수에서 WGWK 카메라 설정 추출.

    지원 형식:
      IIC_WGWK_HOST=192.168.8.101              # 단일 카메라
      IIC_WGWK_NAME=WGWK-AS500J                # optional
      IIC_WGWK_USERNAME=admin
      IIC_WGWK_PASSWORD=123456
      SCF_USERID=...                           # AF on/off용 (SCF 토큰)
      SCF_PASSWD=...

    Returns:
        설정된 카메라 목록. host가 비어 있으면 빈 리스트.
    """
    host = os.environ.get("IIC_WGWK_HOST", "").strip()
    if not host:
        return []
    return [WgwkConfig(
        host=host,
        name=os.environ.get("IIC_WGWK_NAME", "WGWK-AS500J").strip(),
        username=os.environ.get("IIC_WGWK_USERNAME", "admin"),
        password=os.environ.get("IIC_WGWK_PASSWORD", "123456"),
        scf_userid=os.environ.get("SCF_USERID") or None,
        scf_passwd=os.environ.get("SCF_PASSWD") or None,
    )]


def register_static_cameras(
    registry: "CameraRegistry",
    bus: "EventBus",
    configs: list[WgwkConfig],
) -> int:
    """Configured WGWK cameras를 registry에 정적 등록.

    Discovery(pyudev)와 무관하게 startup 시 호출. Camera Attached 이벤트를
    bus로 publish 하여 UI가 자동 감지된 것처럼 보이게 한다.

    Returns:
        새로 등록된 카메라 수.
    """
    added = 0
    for cfg in configs:
        cam = build_camera_model(cfg)
        register_config(cam.id, cfg)
        if registry.add(cam):
            _log.info("wgwk: registered static camera id=%s host=%s name=%s",
                      cam.id, cfg.host, cfg.name)
            bus.publish(CameraAttached(camera=cam))
            added += 1
        else:
            _log.warning("wgwk: camera id=%s already in registry, skip", cam.id)
    return added


# ---------------------------------------------------------------------------
# Capture device — V4L2CaptureDevice-compatible 인터페이스
# ---------------------------------------------------------------------------


class WgwkCaptureDevice:
    """WGWK IP camera capture device. ``V4L2CaptureDevice``와 동일 메서드 시그니처."""

    def __init__(self, config: WgwkConfig) -> None:
        self._config = config
        self._device_path = f"wgwk://{config.host}"
        self._cap: cv2.VideoCapture | None = None
        self._cam = None  # wgwk_camera.Camera lazy-import
        self._negotiated: tuple[int, int, float] = (0, 0, 0.0)

        # Client-side focus counter. None = device 미오픈, otherwise [0, FOCUS_MAX].
        self._focus_pos: int = _FOCUS_DEFAULT
        self._zoom_pos: int = _ZOOM_DEFAULT
        self._af_state: bool = False
        self._focus_lock = threading.Lock()  # focus 명령 직렬화
        self._zoom_lock = threading.Lock()
        # cv2 FFMPEG backend는 cap.read()가 진행 중일 때 다른 thread에서
        # cap.release()를 호출하면 av_read_frame이 freed 구조체에 접근해
        # SIGSEGV. read와 release를 lock으로 직렬화하고 read에 timeout을
        # 설정해 release 대기 시간을 bound.
        self._cap_lock = threading.Lock()

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
        """wgwk_camera 라이브러리로 HAPI login + RTSP cv2 capture open.

        ``width``, ``height``, ``fps``, ``fourcc``는 RTSP 스트림에서는 카메라가
        결정하므로 무시한다. 실제 negotiated 값은 cv2.CAP_PROP_*로 readback.
        """
        try:
            from wgwk_camera import Camera as WgwkCam
        except ImportError as e:
            raise RuntimeError(
                "wgwk_camera library not installed. "
                "Install with: pip install -e /home/jeongmin/Downloads/optical_zoom"
            ) from e

        # 1. HAPI login (cam.admin / focus / zoom 명령용)
        try:
            self._cam = WgwkCam(
                self._config.host,
                username=self._config.username,
                password=self._config.password,
                scf_userid=self._config.scf_userid,
                scf_passwd=self._config.scf_passwd,
                auto_login=True,
            )
        except Exception as e:
            raise CameraBusy(
                f"failed to login to wgwk camera {self._config.host}: {e}"
            ) from e

        # 2. RTSP open
        url = self._cam.video_main().url
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            self._cam.close()
            self._cam = None
            raise CameraBusy(
                f"failed to open RTSP stream from {self._config.host} "
                "(network unreachable or stream limit reached?)"
            )
        # latency 최소화
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        # FFMPEG read timeout — cap.read()가 무한 block되지 않도록.
        # 안전한 release 직렬화를 위해 필요 (release 대기 시간 bound).
        try:
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 1000)
        except Exception:
            pass

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS))

        self._cap = cap
        self._negotiated = (actual_w, actual_h, actual_fps)
        _log.info(
            "wgwk opened: host=%s rtsp=%s negotiated=%dx%d@%.1f",
            self._config.host, url.split("@")[-1],
            actual_w, actual_h, actual_fps,
        )

        # AF 현재 상태 read (SCF 토큰 있는 경우만)
        if self._config.scf_userid and self._config.scf_passwd:
            try:
                af_info = self._cam._image.get_af()
                self._af_state = bool(af_info.get("enable", 0))
            except Exception as e:
                _log.debug("wgwk: initial AF state read failed: %s", e)

        return self._negotiated

    def read(self) -> "tuple[bool, np.ndarray | None]":
        # Snapshot reference outside lock; if release() flipped _cap to None,
        # the snapshot is still a valid object — but to prevent concurrent
        # cap.release() while av_read_frame is in flight, hold the lock for
        # the entire read.
        with self._cap_lock:
            cap = self._cap
            if cap is None:
                return False, None
            return cap.read()

    def release(self) -> None:
        # Lock 동안 cv2 cap을 release. 진행 중인 read()가 끝날 때까지 대기.
        # read timeout이 설정되어 있어 worst case 1초 내에 끝남.
        with self._cap_lock:
            cap = self._cap
            self._cap = None
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None
        _log.info("wgwk released: host=%s", self._config.host)

    # ----- focus -----

    def get_focus(self) -> int | None:
        if self._cap is None:
            return None
        return self._focus_pos

    def set_focus(self, value: int) -> int | None:
        """Client-side counter 갱신 + HAPI focus_near/far(|delta| × MS_PER_UNIT).

        value 범위는 controller에서 이미 clamp됨([0, FOCUS_MAX]).
        """
        if self._cap is None or self._cam is None:
            return None
        target = max(_FOCUS_MIN, min(_FOCUS_MAX, int(value)))
        with self._focus_lock:
            delta = target - self._focus_pos
            if delta == 0:
                return self._focus_pos
            ms = abs(delta) * _MS_PER_FOCUS_UNIT
            # autostop cap ~5s; 큰 delta는 cap
            ms = min(ms, 4500)
            try:
                if delta > 0:
                    self._cam.focus_far(ms)
                else:
                    self._cam.focus_near(ms)
            except Exception as e:
                _log.warning("wgwk set_focus(%d→%d) failed: %s",
                             self._focus_pos, target, e)
                return self._focus_pos
            self._focus_pos = target
        return self._focus_pos

    # ----- autofocus -----

    def get_autofocus(self) -> bool | None:
        if self._cap is None:
            return None
        return self._af_state

    def set_autofocus(self, enabled: bool) -> bool | None:
        if self._cap is None or self._cam is None:
            return None
        if not (self._config.scf_userid and self._config.scf_passwd):
            _log.warning("wgwk set_autofocus: SCF tokens not configured, skip")
            return None
        try:
            self._cam._image.set_af(enable=enabled)
            self._af_state = bool(enabled)
            return self._af_state
        except Exception as e:
            _log.warning("wgwk set_autofocus(%s) failed: %s", enabled, e)
            return None

    # ----- zoom (optical, KF 단위) -----
    #
    # wgwk_camera는 모터 absolute encoder readback이 불가하여 SW-side KF
    # 추정기를 사용. UI 슬라이더 값을 그대로 client-side 카운터로 채택하고
    # 슬라이더의 이전 값과의 delta만큼 zoom_in/zoom_out 명령 발사.

    def get_zoom(self) -> int | None:
        if self._cap is None:
            return None
        return self._zoom_pos

    def set_zoom(self, value: int) -> int | None:
        if self._cap is None or self._cam is None:
            return None
        target = max(_ZOOM_MIN, min(_ZOOM_MAX, int(value)))
        with self._zoom_lock:
            delta_kf = target - self._zoom_pos
            if delta_kf == 0:
                return self._zoom_pos
            # 실측 185ms/KF (wgwk_camera ZoomTracker 기본값). cam의 실제 값 사용.
            ms_per_kf = float(getattr(self._cam._zoom, "ms_per_kf", 185.0))
            ms = int(abs(delta_kf) * ms_per_kf)
            try:
                if delta_kf > 0:
                    self._cam.zoom_in(ms)
                else:
                    self._cam.zoom_out(ms)
            except Exception as e:
                _log.warning("wgwk set_zoom(%d→%d) failed: %s",
                             self._zoom_pos, target, e)
                return self._zoom_pos
            self._zoom_pos = target
        return self._zoom_pos

    # ----- power_line_frequency: not applicable -----

    def get_power_line_frequency(self) -> int | None:
        return None

    def set_power_line_frequency(self, value: int) -> int | None:
        return None
