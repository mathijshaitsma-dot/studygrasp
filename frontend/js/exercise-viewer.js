import { api } from "./api.js";
import { el, icon } from "./util.js";
import { t } from "./i18n.js";

// Eén viewer in de pagina én in het grote dialoogvenster: dezelfde pagina,
// zoom en authenticatie blijven daardoor behouden.
export function exerciseViewer(hash, totalPages) {
  const total = Math.max(1, Number(totalPages) || 1);
  let current = 0;
  let zoom = 1;
  const image = el("img", { class: "exercise-img", alt: "", draggable: "false" });
  const canvas = el("div", { class: "exercise-canvas" }, image);
  const stage = el("div", { class: "exercise-stage", tabindex: "0" }, canvas);
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
    else { dialog.append(viewer); dialog.showModal(); expand.textContent = t("close"); }
  } }, t("exercise_expand"));
  const toolbar = el("div", { class: "exercise-toolbar" }, prev, select, next,
    el("span", { class: "exercise-toolbar-spacer" }), zoomOut, zoomValue, zoomIn, fit, expand);
  const viewer = el("div", { class: "exercise-viewer" }, toolbar, stage, status);
  const dialog = el("dialog", { class: "exercise-dialog", "aria-label": t("exercise_expand") });
  const host = el("div", { class: "exercise-viewer-host" }, viewer, dialog);
  dialog.addEventListener("close", () => { host.prepend(viewer); expand.textContent = t("exercise_expand"); });
  select.addEventListener("change", () => setPage(Number(select.value)));
  stage.addEventListener("wheel", e => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); setZoom(zoom + (e.deltaY < 0 ? 0.1 : -0.1));
  }, { passive: false });
  function setZoom(value) {
    zoom = Math.min(4, Math.max(0.5, Math.round(value * 100) / 100));
    canvas.style.width = `${zoom * 100}%`;
    zoomValue.textContent = `${Math.round(zoom * 100)}%`;
    zoomOut.disabled = zoom <= 0.5;
    zoomIn.disabled = zoom >= 4;
  }
  async function setPage(value) {
    current = Math.min(total - 1, Math.max(0, value));
    select.value = String(current);
    prev.disabled = current === 0;
    next.disabled = current === total - 1;
    image.alt = t("exercise_page", { n: current + 1 });
    stage.scrollTop = 0; stage.scrollLeft = 0;
    status.textContent = "";
    const requested = current;
    try { await api.setSlideImage(image, hash, current); }
    catch (err) { if (current === requested) status.textContent = err.message; }
  }
  setZoom(1); setPage(0);
  return { element: host, setPage, getPage: () => current };
}
