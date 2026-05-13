(() => {
  "use strict";

  const main = document.querySelector(".camera-page");
  const cameraId = main.dataset.cameraId;

  const img = document.getElementById("stream");
  const streamWrap = document.getElementById("stream-wrap");
  const zoomIndicator = document.getElementById("zoom-indicator");
  const status = document.getElementById("stream-status");
  const slider = document.getElementById("focus-slider");
  const focusValue = document.getElementById("focus-value");
  const focusHint = document.getElementById("focus-hint");
  const afToggle = document.getElementById("af-toggle");
  const zoomGroup = document.getElementById("zoom-group");
  const zoomSlider = document.getElementById("zoom-slider");
  const zoomValue = document.getElementById("zoom-value");
  const zoomHint = document.getElementById("zoom-hint");
  const zoomButtons = document.getElementById("zoom-buttons");
  const zoomInBtn = document.getElementById("zoom-in-btn");
  const zoomOutBtn = document.getElementById("zoom-out-btn");
  const labelInput = document.getElementById("label-input");
  const extSelect = document.getElementById("ext-select");
  const captureBtn = document.getElementById("capture-btn");
  const captureStatus = document.getElementById("capture-status");
  const resolutionSelect = document.getElementById("resolution-select");
  const resolutionStatus = document.getElementById("resolution-status");
  const plfGroup = document.getElementById("plf-group");
  const plfSelect = document.getElementById("plf-select");
  const plfStatus = document.getElementById("plf-status");

  let controlsLoaded = false;
  let firstFrame = false;
  let capturing = false;
  let changingResolution = false;
  // "toggle" = native V4L2 AF on/off, "software" = our sweep, null = unsupported
  let afMode = null;
  let afRunning = false;
  // In-flight adaptive pattern for focus slider.
  let pendingFocus = null;
  let inFlight = false;
  // Same pattern for optical zoom slider (absolute mode).
  let pendingZoom = null;
  let zoomInFlight = false;
  // Relative mode press-and-hold state.
  // Tap (< 300ms): 미세 1 KF step (200ms 모터 명령).
  // Hold (>= 300ms): 연속 모션 (4500ms autostop + 3.5초마다 재발사).
  const ZOOM_TAP_MS = 200;        // motor command duration for a brief tap (~1 KF)
  const ZOOM_HOLD_DELAY_MS = 300; // tap → hold 승격 임계
  const ZOOM_HOLD_REFRESH_MS = 3500; // 4500ms autostop에 1초 여유
  let zoomHoldDir = null;          // 현재 누르고 있는 방향
  let zoomHoldUpgradeTimer = null; // tap → hold 승격 setTimeout
  let zoomHoldRefreshTimer = null; // hold 모드 setInterval
  let zoomEnteredHoldMode = false; // true이면 release 시 zoom_step:stop 필요

  img.addEventListener("load", () => {
    if (!firstFrame) {
      firstFrame = true;
      status.textContent = "";
      status.hidden = true;
      loadControls();
      loadStreamConfig();
    }
  });
  img.addEventListener("error", () => {
    if (capturing || changingResolution) return;
    status.textContent = "스트림 연결 실패 또는 종료";
    status.classList.add("error");
    status.hidden = false;
  });

  // ---------------------------------------------------------------------
  // Stream resolution
  // ---------------------------------------------------------------------

  async function loadStreamConfig() {
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/stream-config`
      );
      if (!resp.ok) return;
      const data = await resp.json();
      const current = data.preferred
        ? `${data.preferred[0]}x${data.preferred[1]}`
        : `${img.naturalWidth}x${img.naturalHeight}`;
      // Best-effort select if a matching option exists
      for (const opt of resolutionSelect.options) {
        if (opt.value === current) {
          opt.selected = true;
          break;
        }
      }
    } catch (err) {
      console.warn("stream-config fetch error:", err);
    }
  }

  resolutionSelect.addEventListener("change", async () => {
    const value = resolutionSelect.value;
    const [w, h] = value.split("x").map((n) => parseInt(n, 10));
    if (!w || !h) return;

    changingResolution = true;
    resolutionStatus.hidden = false;
    resolutionStatus.classList.remove("error");
    resolutionStatus.textContent = `해상도 변경하는 중 (${w}×${h})…`;

    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/stream-config`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ width: w, height: h }),
        }
      );
      if (!resp.ok) {
        const detail = await safeDetail(resp);
        throw new Error(detail);
      }
      // Server torn down active source. Re-establish the stream.
      restartStream(`해상도 적용 중 (${w}×${h})…`);
      // Hide hint once first frame at new resolution arrives.
      img.addEventListener(
        "load",
        () => {
          resolutionStatus.hidden = true;
        },
        { once: true }
      );
    } catch (err) {
      resolutionStatus.classList.add("error");
      resolutionStatus.textContent = `해상도 변경 실패: ${err.message}`;
    } finally {
      changingResolution = false;
    }
  });

  // ---------------------------------------------------------------------
  // Controls
  // ---------------------------------------------------------------------

  async function loadControls() {
    if (controlsLoaded) return;
    try {
      const resp = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/controls`);
      if (!resp.ok) {
        console.warn("controls fetch failed:", resp.status);
        return;
      }
      const data = await resp.json();
      applyControls(data);
      controlsLoaded = true;
    } catch (err) {
      console.error("controls fetch error:", err);
    }
  }

  function applyControls(data) {
    if (data.focus) {
      slider.min = data.focus.min;
      slider.max = data.focus.max;
      slider.step = data.focus.step;
      slider.value = data.focus.value ?? data.focus.default;
      slider.disabled = false;
      focusValue.textContent = slider.value;
    } else {
      focusHint.textContent = "이 카메라는 수동 포커스를 지원하지 않습니다.";
      focusHint.hidden = false;
    }
    if (data.autofocus && data.autofocus.supported) {
      // Camera exposes native V4L2 AF — use the toggle pathway.
      afMode = "toggle";
      afToggle.disabled = false;
      afToggle.classList.toggle("on", !!data.autofocus.enabled);
      afToggle.title = "자동 포커스 모드 켜기/끄기";
    } else if (data.focus) {
      // No native AF but manual focus exists → enable software AF (sweep).
      afMode = "software";
      afToggle.disabled = false;
      afToggle.textContent = "AF 실행";
      afToggle.title = "소프트웨어 자동 초점: focus 범위를 sweep하여 최적값 적용 (~3초)";
    }

    if (data.power_line_frequency) {
      plfSelect.replaceChildren();
      for (const opt of data.power_line_frequency.options) {
        const o = document.createElement("option");
        o.value = String(opt.value);
        o.textContent = opt.label;
        if (opt.value === data.power_line_frequency.value) o.selected = true;
        plfSelect.appendChild(o);
      }
      plfGroup.hidden = false;
    } else {
      plfGroup.hidden = true;
    }

    if (data.zoom) {
      const mode = data.zoom.mode || "absolute";
      if (mode === "absolute") {
        zoomSlider.min = data.zoom.min;
        zoomSlider.max = data.zoom.max;
        zoomSlider.step = data.zoom.step;
        zoomSlider.value = data.zoom.value ?? data.zoom.default;
        zoomSlider.disabled = false;
        zoomSlider.hidden = false;
        zoomButtons.hidden = true;
        zoomValue.textContent = zoomSlider.value;
      } else {
        // relative — -/+ buttons
        zoomSlider.hidden = true;
        zoomButtons.hidden = false;
        zoomInBtn.disabled = false;
        zoomOutBtn.disabled = false;
        zoomValue.textContent = data.zoom.value ?? "−";
      }
      zoomGroup.hidden = false;
    } else {
      zoomGroup.hidden = true;
    }
  }

  plfSelect.addEventListener("change", async () => {
    const value = parseInt(plfSelect.value, 10);
    if (Number.isNaN(value)) return;
    plfSelect.disabled = true;
    plfStatus.hidden = false;
    plfStatus.classList.remove("error");
    plfStatus.textContent = "적용 중…";
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/controls`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ power_line_frequency: value }),
        }
      );
      if (!resp.ok) {
        const detail = await safeDetail(resp);
        throw new Error(detail);
      }
      const data = await resp.json();
      const applied = data.power_line_frequency;
      if (applied === null || applied === undefined) {
        throw new Error("not applied");
      }
      // Sync option list to server-confirmed value (V4L2 may clamp).
      for (const o of plfSelect.options) {
        o.selected = parseInt(o.value, 10) === applied;
      }
      plfStatus.textContent = `적용됨 (${plfSelect.options[plfSelect.selectedIndex].textContent})`;
      window.setTimeout(() => { plfStatus.hidden = true; }, 2000);
    } catch (err) {
      plfStatus.classList.add("error");
      plfStatus.textContent = `적용 실패: ${err.message}`;
    } finally {
      plfSelect.disabled = false;
    }
  });

  slider.addEventListener("input", () => {
    const value = parseInt(slider.value, 10);
    focusValue.textContent = value;
    pendingFocus = value;
    drainFocus();
  });

  async function drainFocus() {
    if (inFlight || pendingFocus === null) return;
    inFlight = true;
    try {
      while (pendingFocus !== null) {
        const value = pendingFocus;
        pendingFocus = null;
        try {
          const resp = await fetch(
            `/api/cameras/${encodeURIComponent(cameraId)}/controls`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ focus: value }),
            }
          );
          if (!resp.ok) {
            console.warn("focus PATCH failed:", resp.status);
            continue;
          }
          const data = await resp.json();
          if ("focus" in data && data.focus !== null && pendingFocus === null) {
            focusValue.textContent = data.focus;
          }
        } catch (err) {
          console.error("focus PATCH error:", err);
          break;
        }
      }
    } finally {
      inFlight = false;
    }
  }

  zoomSlider.addEventListener("input", () => {
    const value = parseInt(zoomSlider.value, 10);
    zoomValue.textContent = value;
    pendingZoom = value;
    drainZoom();
  });

  // ─── Relative zoom: tap vs hold ───────────────────────────────────
  // 짧은 tap (< 300ms): 200ms 모터 명령으로 ~1 KF 미세 step. 별도 stop 불필요
  //                     (HAPI autostop으로 motor 스스로 정지).
  // 길게 hold (≥ 300ms): 4500ms autostop으로 연속 모션, 3.5s마다 재발사로 유지.
  //                       release 시 zoom_step:"stop" 으로 즉시 정지.

  async function postZoomStep(direction, durationMs) {
    const body = { zoom_step: direction };
    if (durationMs !== undefined && durationMs !== null) {
      body.zoom_step_ms = durationMs;
    }
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/controls`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      );
      if (!resp.ok) {
        console.warn("zoom_step PATCH failed:", resp.status);
        return null;
      }
      const data = await resp.json();
      const result = data.zoom_step;
      if (result && result.estimate_kf !== undefined && result.estimate_kf !== null) {
        zoomValue.textContent = result.estimate_kf;
      }
      return result;
    } catch (err) {
      console.error("zoom_step PATCH error:", err);
      return null;
    }
  }

  function zoomPressStart(direction, btn) {
    if (zoomHoldDir !== null) return;
    zoomHoldDir = direction;
    zoomEnteredHoldMode = false;
    btn.classList.add("active");

    // 1) 즉시 미세 1 KF step 발사 (200ms autostop). HAPI가 자동으로 stop.
    postZoomStep(direction, ZOOM_TAP_MS);

    // 2) 300ms 후에도 누르고 있으면 hold 모드로 승격: 4500ms 연속 발사
    zoomHoldUpgradeTimer = setTimeout(() => {
      zoomEnteredHoldMode = true;
      postZoomStep(direction);  // duration 생략 → 서버 기본 4500ms
      zoomHoldRefreshTimer = setInterval(() => {
        if (zoomHoldDir !== null) postZoomStep(zoomHoldDir);
      }, ZOOM_HOLD_REFRESH_MS);
    }, ZOOM_HOLD_DELAY_MS);
  }

  function zoomPressEnd() {
    if (zoomHoldDir === null) return;
    if (zoomHoldUpgradeTimer !== null) {
      clearTimeout(zoomHoldUpgradeTimer);
      zoomHoldUpgradeTimer = null;
    }
    if (zoomHoldRefreshTimer !== null) {
      clearInterval(zoomHoldRefreshTimer);
      zoomHoldRefreshTimer = null;
    }
    // hold 모드까지 진입했으면 명시적 stop이 필요. tap만이라면 motor가 200ms
    // autostop으로 자동 정지하므로 stop 명령은 불필요(오히려 다음 tap을 방해함).
    if (zoomEnteredHoldMode) {
      postZoomStep("stop");
    }
    zoomHoldDir = null;
    zoomEnteredHoldMode = false;
    zoomInBtn.classList.remove("active");
    zoomOutBtn.classList.remove("active");
  }

  // Pointer events (마우스·터치 통합).
  for (const [btn, dir] of [[zoomInBtn, "in"], [zoomOutBtn, "out"]]) {
    btn.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      btn.setPointerCapture(e.pointerId);
      zoomPressStart(dir, btn);
    });
    btn.addEventListener("pointerup", () => zoomPressEnd());
    btn.addEventListener("pointercancel", () => zoomPressEnd());
    btn.addEventListener("pointerleave", () => {
      if (zoomHoldDir === dir) zoomPressEnd();
    });
  }

  async function drainZoom() {
    if (zoomInFlight || pendingZoom === null) return;
    zoomInFlight = true;
    try {
      while (pendingZoom !== null) {
        const value = pendingZoom;
        pendingZoom = null;
        try {
          const resp = await fetch(
            `/api/cameras/${encodeURIComponent(cameraId)}/controls`,
            {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ zoom: value }),
            }
          );
          if (!resp.ok) {
            console.warn("zoom PATCH failed:", resp.status);
            continue;
          }
          const data = await resp.json();
          if ("zoom" in data && data.zoom !== null && pendingZoom === null) {
            zoomValue.textContent = data.zoom;
          }
        } catch (err) {
          console.error("zoom PATCH error:", err);
          break;
        }
      }
    } finally {
      zoomInFlight = false;
    }
  }

  afToggle.addEventListener("click", async () => {
    if (afToggle.disabled || afRunning || !afMode) return;
    if (afMode === "toggle") {
      await runNativeAfToggle();
    } else if (afMode === "software") {
      await runSoftwareAf();
    }
  });

  async function runNativeAfToggle() {
    const turningOn = !afToggle.classList.contains("on");
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/controls`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ autofocus: turningOn }),
        }
      );
      if (!resp.ok) return;
      const data = await resp.json();
      if (data.autofocus !== null && data.autofocus !== undefined) {
        afToggle.classList.toggle("on", !!data.autofocus);
        slider.disabled = !!data.autofocus;
      }
    } catch (err) {
      console.error("autofocus PATCH error:", err);
    }
  }

  async function runSoftwareAf() {
    afRunning = true;
    const originalText = afToggle.textContent;
    const originalTitle = afToggle.title;
    afToggle.disabled = true;
    slider.disabled = true;
    captureBtn.disabled = true;
    afToggle.textContent = "AF 실행 중…";
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/autofocus`,
        { method: "POST" }
      );
      if (!resp.ok) {
        const detail = await safeDetail(resp);
        throw new Error(detail);
      }
      const data = await resp.json();
      slider.value = data.focus;
      focusValue.textContent = data.focus;
      afToggle.title =
        `AF 완료: focus=${data.focus}, sharpness=${data.sharpness.toFixed(1)}, ${data.elapsed_ms}ms (${data.attempts} step)`;
    } catch (err) {
      console.error("software AF failed:", err);
      afToggle.title = `AF 실패: ${err.message}`;
      window.setTimeout(() => { afToggle.title = originalTitle; }, 4000);
    } finally {
      afRunning = false;
      afToggle.textContent = originalText;
      afToggle.disabled = false;
      slider.disabled = false;
      captureBtn.disabled = false;
    }
  }

  // ---------------------------------------------------------------------
  // Capture (shutter)
  // ---------------------------------------------------------------------

  captureBtn.addEventListener("click", async () => {
    if (captureBtn.disabled) return;
    capturing = true;
    captureBtn.disabled = true;
    captureStatus.hidden = false;
    captureStatus.classList.remove("success", "error");
    captureStatus.textContent = "촬영 중…";

    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cameraId)}/capture`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            label: labelInput.value,
            ext: extSelect.value,
          }),
        }
      );
      if (!resp.ok) {
        const detail = await safeDetail(resp);
        throw new Error(detail);
      }
      const data = await resp.json();
      captureStatus.classList.add("success");
      captureStatus.textContent =
        `저장됨: ${data.filename} (${formatBytes(data.size)}, ${data.resolution[0]}×${data.resolution[1]})`;
    } catch (err) {
      captureStatus.classList.add("error");
      captureStatus.textContent = `촬영 실패: ${err.message}`;
    } finally {
      captureBtn.disabled = false;
      capturing = false;
      // Capture no longer tears down the stream — no restart needed.
    }
  });

  function restartStream(statusText) {
    firstFrame = false;
    status.classList.remove("error");
    status.textContent = statusText || "스트림 재연결 중…";
    status.hidden = false;
    img.src = `/stream/${encodeURIComponent(cameraId)}?t=${Date.now()}`;
    resetZoom();
  }

  // ---------------------------------------------------------------------
  // Pan / zoom (client-side CSS transform)
  // ---------------------------------------------------------------------

  const ZOOM_MIN = 1;
  const ZOOM_MAX = 10;
  const ZOOM_STEP = 1.15;

  let zoomScale = 1;
  let panX = 0;
  let panY = 0;
  let dragging = false;
  let dragStartX = 0;
  let dragStartY = 0;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function applyZoom() {
    const imgW = img.offsetWidth;
    const imgH = img.offsetHeight;
    const sw = imgW * zoomScale;
    const sh = imgH * zoomScale;
    // pan range: scaled image's left/top can go as low as (container - scaled),
    // and at most 0 (image's left/top stuck to container's left/top).
    const minX = Math.min(0, imgW - sw);
    const minY = Math.min(0, imgH - sh);
    panX = clamp(panX, minX, 0);
    panY = clamp(panY, minY, 0);
    img.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomScale})`;

    const pct = Math.round(zoomScale * 100);
    zoomIndicator.textContent = `${pct}%`;
    zoomIndicator.hidden = zoomScale === 1;
    streamWrap.classList.toggle("zoomed", zoomScale > 1);
  }

  function resetZoom() {
    zoomScale = 1;
    panX = 0;
    panY = 0;
    applyZoom();
  }

  streamWrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = streamWrap.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    const newScale = clamp(zoomScale * factor, ZOOM_MIN, ZOOM_MAX);
    // Keep the pixel under the cursor visually fixed.
    panX = mx - (mx - panX) * (newScale / zoomScale);
    panY = my - (my - panY) * (newScale / zoomScale);
    zoomScale = newScale;
    applyZoom();
  }, { passive: false });

  streamWrap.addEventListener("mousedown", (e) => {
    if (zoomScale <= 1) return;
    if (e.button !== 0) return;
    dragging = true;
    dragStartX = e.clientX - panX;
    dragStartY = e.clientY - panY;
    streamWrap.classList.add("grabbing");
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    panX = e.clientX - dragStartX;
    panY = e.clientY - dragStartY;
    applyZoom();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    streamWrap.classList.remove("grabbing");
  });

  streamWrap.addEventListener("dblclick", () => {
    resetZoom();
  });

  // Re-clamp on viewport resize (container size may change).
  window.addEventListener("resize", () => {
    if (zoomScale > 1) applyZoom();
  });

  async function safeDetail(resp) {
    try {
      const j = await resp.json();
      return j.detail || `HTTP ${resp.status}`;
    } catch {
      return `HTTP ${resp.status}`;
    }
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
})();
