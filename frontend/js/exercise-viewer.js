import { api } from "./api.js";
import { el, icon } from "./util.js";
import { t } from "./i18n.js";
import { attachSlideGestures } from "./slide-gestures.js";

export function exerciseViewer(hash, totalPages, initialPage = 0) {
  const total = Math.max(1, Number(totalPages) || 1);
  let current = Math.min(total - 1, Math.max(0, initialPage));
  let zoom = 1;
  let ratio = 0.707;
  const image = el("img", { class: "exercise-img", alt: "", draggable: "false" });
  const canvas = el("div", { class: "slide-frame exercise-canvas" }, image);
  const stage = el("div", { class: "slide-holder exercise-stage is-loading", tabindex: "0" }, canvas,
    el("div", { class: "exercise-page-loading" }, el("div", { class: "spinner" })));
  const status = el("div", { class: "exercise-viewer-status", role: "status" });
  const pageLabel = el("span", { class: "page-label", "aria-live": "polite" });
  const prev = el("button", { class: "nav-btn", "aria-label": t("prev"), onclick: () => setPage(current - 1) },
    icon("left", "sm"), el("span", { class: "lbl" }, t("prev")));
  const next = el("button", { class: "nav-btn", "aria-label": t("next"), onclick: () => setPage(current + 1) },
    el("span", { class: "lbl" }, t("next")), icon("right", "sm"));
  const progress = el("div", { class: "progress-fill" });
  const overview = el("button", { class: "nav-btn", "aria-label": t("exercise_all_pages"), title: t("exercise_all_pages"), onclick: showPages }, icon("grid", "sm"));
  const nav = el("div", { class: "stage-nav" },
    el("div", { class: "tools" }, overview), el("div", { class: "spacer" }), prev, pageLabel, next,
    el("div", { class: "spacer" }), el("div", { class: "progress-track stage-progress" }, progress));
  const viewer = el("div", { class: "exercise-viewer stage" }, stage, status, nav);
  const host = el("div", { class: "exercise-viewer-host" }, viewer);

  function fitCanvas() {
    const styles = getComputedStyle(stage);
    const width = Math.max(1, stage.clientWidth - parseFloat(styles.paddingLeft) - parseFloat(styles.paddingRight));
    const height = Math.max(1, stage.clientHeight - parseFloat(styles.paddingTop) - parseFloat(styles.paddingBottom));
    canvas.style.width = `${Math.round(Math.min(width, height * ratio) * zoom)}px`;
    canvas.style.flex = "none";
    stage.classList.toggle("zoomed", zoom > 1.001);
  }
  function setScale(value, anchorX = null, anchorY = null) {
    const nextScale = Math.round(Math.max(1, Math.min(4, value)) * 1000) / 1000;
    if (Math.abs(nextScale - zoom) < 0.0005) return;
    const before = image.getBoundingClientRect();
    const holder = stage.getBoundingClientRect();
    const x = anchorX ?? holder.left + holder.width / 2;
    const y = anchorY ?? holder.top + holder.height / 2;
    const relX = Math.max(0, Math.min(1, (x - before.left) / Math.max(1, before.width)));
    const relY = Math.max(0, Math.min(1, (y - before.top) / Math.max(1, before.height)));
    zoom = nextScale;
    fitCanvas();
    if (zoom <= 1.001) { stage.scrollLeft = stage.scrollTop = 0; return; }
    const after = image.getBoundingClientRect();
    stage.scrollLeft += after.left + relX * after.width - x;
    stage.scrollTop += after.top + relY * after.height - y;
  }
  attachSlideGestures(stage, image, { getScale: () => zoom, setScale, navigate: delta => setPage(current + delta) });
  stage.addEventListener("keydown", event => {
    if (event.target !== stage) return;
    if (event.key === "ArrowRight" || event.key === " ") { event.preventDefault(); setPage(current + 1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); setPage(current - 1); }
  });
  image.addEventListener("load", () => {
    stage.classList.remove("is-loading");
    ratio = image.naturalWidth / Math.max(1, image.naturalHeight);
    fitCanvas();
    host.dispatchEvent(new CustomEvent("exercise:page-size", { bubbles: true, detail: { ratio } }));
  });
  const observer = new ResizeObserver(() => {
    if (!host.isConnected) { observer.disconnect(); return; }
    fitCanvas();
  });
  observer.observe(stage);

  function showPages() {
    const grid = el("div", { class: "exercise-page-grid" });
    const dialog = el("dialog", { class: "exercise-dialog", "aria-label": t("exercise_all_pages") },
      el("div", { class: "exercise-workspace-head" }, el("strong", {}, t("exercise_all_pages")),
        el("button", { class: "btn", onclick: () => dialog.close() }, t("close"))), grid);
    const thumbnails = new IntersectionObserver(entries => {
      for (const entry of entries) if (entry.isIntersecting) {
        const thumb = entry.target;
        thumbnails.unobserve(thumb);
        api.setSlideImage(thumb, hash, Number(thumb.dataset.page)).catch(() => {});
      }
    });
    for (let i = 0; i < total; i++) {
      const thumb = el("img", { alt: t("exercise_page", { n: i + 1 }), dataset: { page: i }, loading: "lazy" });
      grid.append(el("button", { class: `exercise-page-tile${i === current ? " on" : ""}`, onclick: () => { dialog.close(); setPage(i); } },
        thumb, el("span", {}, t("exercise_page", { n: i + 1 }))));
      thumbnails.observe(thumb);
    }
    dialog.addEventListener("close", () => { thumbnails.disconnect(); dialog.remove(); }, { once: true });
    document.body.append(dialog); dialog.showModal();
  }
  async function setPage(value) {
    const nextPage = Math.min(total - 1, Math.max(0, value));
    if (current === nextPage && image.getAttribute("src")) return;
    if (zoom > 1.2) { zoom = 1; fitCanvas(); }
    current = nextPage;
    stage.classList.add("is-loading");
    pageLabel.textContent = `${t("exercise_page", { n: current + 1 })} / ${total}`;
    progress.style.width = `${(current + 1) / total * 100}%`;
    prev.disabled = current === 0; next.disabled = current === total - 1;
    image.alt = t("exercise_page", { n: current + 1 });
    stage.scrollTop = stage.scrollLeft = 0;
    status.textContent = "";
    const requested = current;
    try { await api.setSlideImage(image, hash, current, "ai"); }
    catch (err) { if (current === requested) { stage.classList.remove("is-loading"); status.textContent = err.message; } }
  }
  setPage(current);
  return { element: host, setPage, getPage: () => current };
}
