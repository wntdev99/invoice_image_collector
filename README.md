# invoice_image_collector

LAN 내부 USB 카메라를 웹 브라우저에서 제어·캡처하고, 갤러리에서 ZIP으로 묶어 받을 수 있는 FastAPI 기반 도구.

## 기능

- 카메라 hot-plug 자동 감지 (Server-Sent Events)
- MJPEG 라이브 프리뷰, **스트림 해상도를 사용자가 직접 선택**
- 수동 포커스 슬라이더 (in-flight 적응형, 사실상 실시간)
- **소프트웨어 자동 초점** — 표준 V4L2 AF가 없는 카메라에서 Laplacian variance + coarse-to-fine sweep으로 직접 AF 수행
- **Anti-flicker** (`power_line_frequency`) — 카메라 펌웨어가 노출할 때만 활성 (capability-gated)
- 셔터 — 현재 스트림 프레임을 jpg/webp로 즉시 저장 (≈15ms)
- 갤러리 — 썸네일 그리드, 라이트박스, 체크박스 다중 선택, 선택분 ZIP 다운로드, 선택 삭제, path-traversal 차단

## 시스템 요구사항

- Linux (V4L2 / udev)
- Python 3.11+
- UVC 호환 USB 카메라. 본 프로젝트는 다음으로 검증:
  - Arducam B0478 (48MP IMX586, USB3)
  - Chicony Integrated Camera (노트북 내장)

## 빠른 시작

```bash
# 1) 의존성·venv 자동 설치 (apt 단계에서만 sudo 비밀번호)
./script/setup.sh

# 2) (선택) Arducam B0478 udev 규칙 — 시리얼 기반 안정적 심볼릭 링크 + 권한
sudo ./script/setup_arducam_udev.sh

# 3) 서버 실행
./script/run.sh
```

브라우저에서 `http://<호스트-IP>:8001`.

기본 포트는 8001. 변경하려면:
```bash
IIC_PORT=8002 ./script/run.sh
```

개발 시 자동 리로드:
```bash
./script/run.sh --reload
```

## 부팅 시 자동 시작 (systemd)

`script/iic.service`를 환경에 맞게 편집 후 (User, WorkingDirectory, ExecStart 경로 등):

```bash
sudo cp script/iic.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iic.service

# 상태/로그
systemctl status iic.service
journalctl -u iic.service -f
```

## 환경변수

`app.config.Settings`가 읽는 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `IIC_HOST` | `0.0.0.0` | listen host (script/run.sh가 그대로 사용) |
| `IIC_PORT` | `8000` | listen port (script/run.sh는 8001을 기본으로 override) |
| `IIC_STORAGE_DIR` | `~/Pictures/invoice_image_collector` | 촬영 이미지 저장 경로 |

## 디렉토리 구조

```
app/
├── camera/        카메라 검출 (pyudev), V4L2 capture/controller, 모델/에러
├── stream/        MJPEG provider, lifecycle coordinator, base Protocol
├── capture/       셔터, 인코딩(jpg/webp), 소프트웨어 AF, AF 안정화
├── storage/       파일 enumerate, 썸네일(LRU 캐시), ZIP 패키징, 파일명 규칙
├── api/           FastAPI 라우터 (cameras, controls, stream, capture, events, autofocus, images)
├── core/          in-process EventBus (단방향 pub/sub)
└── web/           Jinja2 템플릿 + 정적 자산 (CSS/JS)

script/            setup.sh, run.sh, setup_arducam_udev.sh, iic.service
```

## 주요 페이지 / API

### 페이지

| 경로 | 용도 |
|---|---|
| `GET /` | 카메라 카드 그리드 (실시간 hot-plug) |
| `GET /cam/{id}` | 카메라 상세 — 프리뷰 + 컨트롤 패널 + 셔터 |
| `GET /gallery` | 갤러리 — 썸네일·선택 ZIP/삭제·라이트박스 |

### API

| 메서드 | 경로 | 용도 |
|---|---|---|
| `GET` | `/api/cameras` | 등록된 카메라 목록 (capabilities 포함) |
| `GET` | `/api/cameras/{id}` | 단일 카메라 상세 |
| `GET` | `/events` | SSE — `camera_attached`/`camera_detached` |
| `GET` | `/stream/{id}` | MJPEG multipart 스트림 |
| `GET`/`PUT` | `/api/cameras/{id}/stream-config` | 스트림 해상도 조회/변경 |
| `GET`/`PATCH` | `/api/cameras/{id}/controls` | focus / autofocus / power_line_frequency |
| `POST` | `/api/cameras/{id}/autofocus` | 소프트웨어 AF sweep 실행 |
| `POST` | `/api/cameras/{id}/capture` | 셔터 — 현재 프레임 인코딩·저장 |
| `GET` | `/api/images` | 저장된 이미지 목록 (mtime 역순) |
| `GET` | `/api/images/{name}/thumb` | on-the-fly 썸네일 (240px max edge, LRU 캐시) |
| `GET` | `/api/images/{name}` | 원본 다운로드 |
| `DELETE` | `/api/images` | body `{names: [...]}` — 다중 삭제 |
| `POST` | `/api/images/zip` | body `{names: [...]}` — ZIP 스트리밍 다운로드 |
| `GET` | `/healthz` | liveness probe |

## 아키텍처 메모

- **단일 active 카메라 정책**: 한 번에 한 카메라만 V4L2를 점유. `/cam/{id}` 진입 시 자동 전환, 이전 스트림은 자동 종료.
- **스트림 해상도와 캡처 해상도 동일**: 셔터 시점에 디바이스를 reopen하지 않고 **현재 스트림 프레임을 그대로 인코딩**. 검정 첫 프레임/AE 미안정/긴 latency 문제 회피. 사용자는 dropdown으로 스트림 해상도를 자유롭게 변경.
- **Capability-gated UI**: 카메라가 V4L2 ctrl(`focus_absolute`, `power_line_frequency` 등)을 노출할 때만 해당 컨트롤이 UI에 보입니다. 예: Arducam B0478 구 펌웨어는 `power_line_frequency` 미노출 → dropdown 숨김.
- **Cleanup 안전성**: 클라이언트 disconnect 시 FastAPI가 generator를 cancel하지만, `FrameSource.close()`가 동기 메서드라 cleanup이 cancelled context에서도 끝까지 실행됨 (RCA로 발견·수정한 패턴).
- **추상화 경계**: `StreamProvider` Protocol을 두어 향후 WebRTC 등으로 1차 구현을 교체 가능. 현재는 `MJPEGStreamProvider`.

## Arducam B0478 펌웨어별 차이 (참고)

같은 모델이라도 펌웨어 date code에 따라 노출되는 V4L2 컨트롤이 다릅니다:

| 펌웨어 | `power_line_frequency` | `exposure_time_absolute` max | 비고 |
|---|---|---|---|
| 2024-12-20 (구) | 미노출 | 330 (33ms) | UI에서 anti-flicker 비활성 |
| 2025-10-31 (신) | 노출 (0/50Hz/60Hz) | 3333 (333ms) | UI에서 anti-flicker 정상 |

위 차이는 `Capabilities` probe로 자동 탐지되어 UI가 적절히 분기합니다.

## 라이선스

(미정)
