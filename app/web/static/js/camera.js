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

  let controlsLoaded = false;
  let firstFrame = false;
  let capturing = false;
  // In-flight adaptive pattern for focus slider.
  let pendingFocus = null;
  let inFlight = false;

  img.addEventListener("load", () => {
    if (!firstFrame) {
      firstFrame = true;
      status.textContent = "";
      status.hidden = true;
      loadControls();
    }
  });
  img.addEventListener("error", () => {
    if (capturing) return;  // shutter intentionally tears down the stream
    status.textContent = "스트림 연결 실패 또는 종료";
    status.classList.add("error");
    status.hidden = false;
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
      afToggle.disabled = false;
      afToggle.classList.toggle("on", !!data.autofocus.enabled);
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
    if (afToggle.disabled) return;
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
  });

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
      // Re-establish preview stream after capture's reopen-at-full-res cycle.
      restartStream();
    }
  });

  function restartStream() {
    firstFrame = false;
    status.classList.remove("error");
    status.textContent = "스트림 재연결 중…";
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
