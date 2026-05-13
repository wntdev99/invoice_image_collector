(() => {
  "use strict";

  const cameras = new Map();
  const grid = document.getElementById("camera-grid");
  const emptyState = document.getElementById("empty-state");
  const indicator = document.getElementById("conn-indicator");

  function render() {
    grid.replaceChildren();
    const list = Array.from(cameras.values()).sort((a, b) =>
      a.name.localeCompare(b.name, "ko")
    );
    for (const cam of list) grid.appendChild(renderCard(cam));
    emptyState.hidden = list.length > 0;
  }

  function renderCard(cam) {
    const enabled = cam.enabled !== false;
    const card = document.createElement("article");
    card.className = "card" + (enabled ? "" : " disabled");
    card.tabIndex = enabled ? 0 : -1;
    card.dataset.cameraId = cam.id;

    const open = () => {
      if (!enabled) return;
      window.location.href = `/cam/${encodeURIComponent(cam.id)}`;
    };
    card.addEventListener("click", open);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "card-toggle" + (enabled ? "" : " off");
    toggleBtn.textContent = enabled ? "사용" : "사용 안함";
    toggleBtn.title = enabled
      ? "이 카메라 비활성화 — V4L2 핸들 해제, 다른 앱이 사용 가능"
      : "이 카메라 다시 활성화";
    toggleBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleEnabled(cam, toggleBtn);
    });
    card.appendChild(toggleBtn);

    const title = document.createElement("h2");
    title.className = "card-title";
    title.textContent = cam.name;
    card.appendChild(title);

    const path = document.createElement("p");
    path.className = "card-path";
    path.textContent = cam.device_path;
    card.appendChild(path);

    const chips = document.createElement("div");
    chips.className = "chips";
    if (!enabled) {
      const dis = makeChip("사용 안함");
      dis.classList.add("chip-disabled");
      chips.appendChild(dis);
    }
    const caps = cam.capabilities;
    if (caps.has_autofocus) chips.appendChild(makeChip("AF"));
    if (caps.has_manual_focus) chips.appendChild(makeChip("MF"));
    if (caps.zoom) chips.appendChild(makeChip("ZOOM"));
    const maxRes = caps.resolutions[caps.resolutions.length - 1];
    if (maxRes) chips.appendChild(makeChip(`${maxRes[0]}×${maxRes[1]}`));
    if (caps.formats.length > 0) chips.appendChild(makeChip(caps.formats.join("/")));
    card.appendChild(chips);

    return card;
  }

  async function toggleEnabled(cam, button) {
    const targetEnabled = cam.enabled === false;
    button.disabled = true;
    try {
      const resp = await fetch(
        `/api/cameras/${encodeURIComponent(cam.id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: targetEnabled }),
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const updated = await resp.json();
      cameras.set(updated.id, updated);
      render();
    } catch (err) {
      console.error("toggle failed:", err);
      button.disabled = false;
    }
  }

  function makeChip(text) {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = text;
    return span;
  }

  function setConnected(ok) {
    indicator.classList.toggle("ok", !!ok);
    indicator.classList.toggle("err", !ok);
  }

  async function loadInitial() {
    try {
      const resp = await fetch("/api/cameras");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      cameras.clear();
      for (const cam of data.cameras) cameras.set(cam.id, cam);
      render();
    } catch (err) {
      console.error("failed to load camera list:", err);
    }
  }

  function connectEvents() {
    const es = new EventSource("/events");
    es.addEventListener("ready", () => setConnected(true));
    es.addEventListener("camera_attached", (ev) => {
      const cam = JSON.parse(ev.data);
      cameras.set(cam.id, cam);
      render();
    });
    es.addEventListener("camera_detached", (ev) => {
      const { camera_id } = JSON.parse(ev.data);
      cameras.delete(camera_id);
      render();
    });
    es.onopen = () => {
      setConnected(true);
      // Re-sync from REST on (re)connect so we don't drift if events were missed.
      loadInitial();
    };
    es.onerror = () => setConnected(false);
  }

  loadInitial().then(connectEvents);
})();
