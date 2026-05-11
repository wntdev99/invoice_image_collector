(() => {
  "use strict";

  const main = document.querySelector(".camera-page");
  const cameraId = main.dataset.cameraId;

  const img = document.getElementById("stream");
  const status = document.getElementById("stream-status");
  const slider = document.getElementById("focus-slider");
  const focusValue = document.getElementById("focus-value");
  const focusHint = document.getElementById("focus-hint");
  const afToggle = document.getElementById("af-toggle");
  const labelInput = document.getElementById("label-input");
  const extSelect = document.getElementById("ext-select");
  const captureBtn = document.getElementById("capture-btn");
  const captureStatus = document.getElementById("capture-status");
  const resolutionSelect = document.getElementById("resolution-select");
  const resolutionStatus = document.getElementById("resolution-status");

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
  }

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
  }

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
