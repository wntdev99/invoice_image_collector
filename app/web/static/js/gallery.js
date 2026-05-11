(() => {
  "use strict";

  const grid = document.getElementById("gallery-grid");
  const emptyMsg = document.getElementById("empty-msg");
  const counter = document.getElementById("counter");
  const selectAll = document.getElementById("select-all");
  const selectedCount = document.getElementById("selected-count");
  const deleteBtn = document.getElementById("delete-btn");
  const zipBtn = document.getElementById("zip-btn");
  const actionStatus = document.getElementById("action-status");

  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightbox-img");
  const lightboxInfo = document.getElementById("lightbox-info");
  const lightboxClose = document.getElementById("lightbox-close");

  const state = {
    images: [],
    selected: new Set(),
  };

  load();

  async function load() {
    try {
      const resp = await fetch("/api/images");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      state.images = data.images;
      render();
    } catch (err) {
      flash(`목록 로드 실패: ${err.message}`, true);
    }
  }

  function render() {
    grid.replaceChildren();
    state.selected = new Set(
      [...state.selected].filter((n) => state.images.some((i) => i.name === n))
    );
    counter.textContent = `${state.images.length}장`;
    emptyMsg.hidden = state.images.length > 0;
    for (const item of state.images) {
      grid.appendChild(renderCard(item));
    }
    selectAll.checked =
      state.images.length > 0 && state.selected.size === state.images.length;
    selectAll.indeterminate =
      state.selected.size > 0 && state.selected.size < state.images.length;
    updateSelection();
  }

  function renderCard(item) {
    const card = document.createElement("article");
    card.className = "thumb-card";
    if (state.selected.has(item.name)) card.classList.add("selected");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "thumb-check";
    checkbox.checked = state.selected.has(item.name);
    checkbox.addEventListener("click", (e) => e.stopPropagation());
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(item.name);
      else state.selected.delete(item.name);
      card.classList.toggle("selected", checkbox.checked);
      updateSelection();
    });
    card.appendChild(checkbox);

    const img = document.createElement("img");
    img.className = "thumb-img";
    img.loading = "lazy";
    img.src = `/api/images/${encodeURIComponent(item.name)}/thumb`;
    img.alt = item.name;
    img.addEventListener("click", () => openLightbox(item));
    card.appendChild(img);

    const meta = document.createElement("div");
    meta.className = "thumb-meta";
    const nameEl = document.createElement("span");
    nameEl.className = "thumb-name";
    nameEl.textContent = item.name;
    nameEl.title = item.name;
    const sizeEl = document.createElement("span");
    sizeEl.className = "thumb-size";
    sizeEl.textContent = formatBytes(item.size);
    meta.appendChild(nameEl);
    meta.appendChild(sizeEl);
    card.appendChild(meta);

    return card;
  }

  function updateSelection() {
    const n = state.selected.size;
    selectedCount.textContent = `${n}장 선택`;
    deleteBtn.disabled = n === 0;
    zipBtn.disabled = n === 0;
  }

  selectAll.addEventListener("change", () => {
    if (selectAll.checked) {
      for (const i of state.images) state.selected.add(i.name);
    } else {
      state.selected.clear();
    }
    render();
  });

  deleteBtn.addEventListener("click", async () => {
    const names = [...state.selected];
    if (names.length === 0) return;
    if (!window.confirm(`${names.length}장을 삭제하시겠습니까? 되돌릴 수 없습니다.`)) {
      return;
    }
    deleteBtn.disabled = true;
    try {
      const resp = await fetch("/api/images", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const failed = Object.entries(data.deleted).filter(([_, ok]) => !ok);
      if (failed.length > 0) {
        flash(`${failed.length}장 삭제 실패 (${names.length - failed.length}장 완료)`, true);
      } else {
        flash(`${names.length}장 삭제 완료`, false);
      }
      state.selected.clear();
      await load();
    } catch (err) {
      flash(`삭제 실패: ${err.message}`, true);
      deleteBtn.disabled = false;
    }
  });

  zipBtn.addEventListener("click", async () => {
    const names = [...state.selected];
    if (names.length === 0) return;
    zipBtn.disabled = true;
    flash("ZIP 생성 중…", false);
    try {
      const resp = await fetch("/api/images/zip", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ names }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const disposition = resp.headers.get("Content-Disposition") || "";
      const match = /filename="([^"]+)"/.exec(disposition);
      const filename = match ? match[1] : "images.zip";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      flash(`다운로드: ${filename} (${formatBytes(blob.size)})`, false);
    } catch (err) {
      flash(`ZIP 실패: ${err.message}`, true);
    } finally {
      zipBtn.disabled = state.selected.size === 0;
    }
  });

  // -------------------- Lightbox --------------------

  function openLightbox(item) {
    lightboxImg.src = `/api/images/${encodeURIComponent(item.name)}`;
    lightboxInfo.textContent = `${item.name} · ${formatBytes(item.size)}`;
    lightbox.hidden = false;
    document.body.classList.add("no-scroll");
  }
  function closeLightbox() {
    lightbox.hidden = true;
    lightboxImg.removeAttribute("src");
    document.body.classList.remove("no-scroll");
  }
  lightboxClose.addEventListener("click", closeLightbox);
  lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !lightbox.hidden) closeLightbox();
  });

  // -------------------- Helpers --------------------

  function flash(text, isError) {
    actionStatus.hidden = false;
    actionStatus.textContent = text;
    actionStatus.classList.toggle("error", !!isError);
    actionStatus.classList.toggle("success", !isError);
  }

  function formatBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
})();
