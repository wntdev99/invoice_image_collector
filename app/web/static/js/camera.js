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

  let controlsLoaded = false;
  let firstFrame = false;
  // In-flight adaptive pattern: at most one PATCH in flight at a time.
  // While one is running, additional slider input updates `pendingFocus`; the
  // loop drains the latest value as soon as the previous PATCH responds.
  let pendingFocus = null;
  let inFlight = false;

  img.addEventListener("load", () => {
    if (!firstFrame) {
      firstFrame = true;
      status.textContent = "";
      status.hidden = true;
      // Stream is active → controls endpoint is now addressable.
      loadControls();
    }
  });
  img.addEventListener("error", () => {
    status.textContent = "스트림 연결 실패 또는 종료";
    status.classList.add("error");
    status.hidden = false;
  });

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
          // Only sync UI from server if user hasn't queued a newer value.
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
})();
