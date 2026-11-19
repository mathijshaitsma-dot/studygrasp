import { api } from "./api.js";
import { el, icon } from "./util.js";
import { t } from "./i18n.js";

// Eén viewer in de pagina én in het grote dialoogvenster: dezelfde pagina,
// zoom en authenticatie blijven daardoor behouden.
export function exerciseViewer(hash, totalPages, initialPage = 0) {
  const total = Math.max(1, Number(totalPages) || 1);
  let current = Math.min(total - 1, Math.max(0, initialPage));
  let zoom = 1;
  let ratio = 0.707;
  const image = el("img", { class: "exercise-img", alt: "", draggable: "false" });
  const canvas = el("div", { class: "exercise-canvas" }, image);
  const stage = el("div", { class: "exercise-stage is-loading", tabindex: "0" }, canvas,
    el("div", { class: "exercise-page-loading" }, el("div", { class: "spinner" })));
  const status = el("div", { class: "exercise-viewer-status", role: "status" });
  const select = el("select", { class: "field", "aria-label": t("exercise_page", { n: "" }) },
    ...Array.from({ length: total }, (_, i) => el("option", { value: i }, `${i + 1} / ${total}`)));
  const prev = el("button", { class: "btn", "aria-label": t("prev"), onclick: () => setPage(current - 1) }, icon("left", "sm"));
  const next = el("button", { class: "btn", "aria-label": t("next"), onclick: () => setPage(current + 1) }, icon("right", "sm"));
  const zoomValue = el("span", { class: "exercise-zoom", "aria-live": "polite" });
  const zoomOut = el("button", { class: "btn", "aria-label": t("exercise_zoom_out"), onclick: () => setZoom(zoom - 0.25) }, "−");
  const zoomIn = el("button", { class: "btn", "aria-label": t("exercise_zoom_in"), onclick: () => setZoom(zoom + 0.25) }, "+");
  const fit = el("button", { class: "btn", onclick: () => setZoom(1) }, t("exercise_fit"));
  const expand = el("button", { class: "btn", onclick: () => {
    if (dialog.open) dialog.close();
    else { dialog.append(viewer); dialog.showModal(); expand.textContent = t("close"); requestAnimationFrame(fitCanvas); }
  } }, t("exercise_expand"));
  const toolbar = el("div", { class: "exercise-toolbar" }, prev, select, next,
    el("span", { class: "exercise-toolbar-spacer" }), zoomOut, zoomValue, zoomIn, fit, expand);
  const viewer = el("div", { class: "exercise-viewer" }, toolbar, stage, status);
  const dialog = el("dialog", { class: "exercise-dialog", "aria-label": t("exercise_expand") });
  const host = el("div", { class: "exercise-viewer-host" }, viewer, dialog);
  dialog.addEventListener("close", () => { host.prepend(viewer); expand.textContent = t("exercise_expand"); requestAnimationFrame(fitCanvas); });
  function fitCanvas() {
    const width = Math.max(1, stage.clientWidth - 24);
    const height = Math.max(1, stage.clientHeight - 24);
    canvas.style.width = `${Math.min(width, height * ratio) * zoom}px`;
    canvas.style.flex = "none";
  }
  image.addEventListener("load", () => {
    stage.classList.remove("is-loading");
    ratio = image.naturalWidth / Math.max(1, image.naturalHeight);
    fitCanvas();
    host.dispatchEvent(new CustomEvent("exercise:page-size", { bubbles: true, detail: { ratio } }));
  });
  const resizeObserver = new ResizeObserver(() => {
    if (!host.isConnected) { resizeObserver.disconnect(); return; }
    fitCanvas();
  });
  resizeObserver.observe(stage);
  select.addEventListener("change", () => setPage(Number(select.value)));
  stage.addEventListener("wheel", e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); setZoom(zoom + (e.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });
  function setZoom(value) {
    zoom = Math.min(4, Math.max(0.5, Math.round(value * 100) / 100));
    fitCanvas();
    zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    zoomOut.disabled = zoom <= 0.5;
    zoomIn.disabled = zoom >= 4;
  }
  async function setPage(value) {
    const nextPage = Math.min(total - 1, Math.max(0, value));
    if (current === nextPage && image.getAttribute("src")) return;
    current = nextPage;
    stage.classList.add("is-loading");
    select.value = String(current);
    prev.disabled = current === 0;
    next.disabled = current === total - 1;
    image.alt = t("exercise_page", { n: current + 1 });
    stage.scrollTop = 0; stage.scrollLeft = 0;
    status.textContent = "";
    const requested = current;
    try { await api.setSlideImage(image, hash, current, "ai"); }
    catch (err) { if (current === requested) { stage.classList.remove("is-loading"); status.textContent = err.message; } }
  }
  setZoom(1); setPage(current);
  return { element: host, setPage, getPage: () => current };
}
