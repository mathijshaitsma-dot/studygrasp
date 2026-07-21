// Kleine DOM- en formatteerhulpjes.
import { t, uiLocale } from "./i18n.js";

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(child));
  }
  return node;
}

export function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `icon ${cls}`.trim());
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export function timeAgo(unixSeconds) {
  if (!unixSeconds) return "";
  const diff = Date.now() / 1000 - unixSeconds;
  if (diff < 90) return t("just_now");
  if (diff < 3600) return t("min_ago", { n: Math.round(diff / 60) });
  if (diff < 86400) return t("hours_ago", { n: Math.round(diff / 3600) });
  if (diff < 86400 * 7) return t("days_ago", { n: Math.round(diff / 86400) });
  return new Date(unixSeconds * 1000).toLocaleDateString(uiLocale(), { day: "numeric", month: "short", year: "numeric" });
}

// ---------- toasts ----------
export function toast(message, kind = "info", ms = 3200) {
  const root = document.getElementById("toast-root");
  const iconName = kind === "err" ? "alert" : kind === "ok" ? "check" : "sparkle";
  const node = el("div", { class: `toast ${kind}` }, icon(iconName, "sm"), message);
  root.append(node);
  setTimeout(() => {
    node.classList.add("leaving");
    setTimeout(() => node.remove(), 300);
  }, ms);
}

// ---------- modals ----------
export function openModal(content, { center = false, small = false } = {}) {
  const root = document.getElementById("modal-root");
  const scrim = el("div", { class: `modal-scrim${center ? " center" : ""}` });
  const modal = el("div", { class: `modal${small ? " sm" : ""}`, role: "dialog" }, content);
  scrim.append(modal);
  const close = () => { scrim.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) close(); });
  document.addEventListener("keydown", onKey);
  root.append(scrim);
  return close;
}

export function confirmDialog({ title, body, confirmLabel = t("delete"), danger = true }) {
  return new Promise((resolve) => {
    let close;
    const content = el("div", {},
      el("div", { class: "confirm-body" }, el("h3", {}, title), el("p", {}, body)),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => { close(); resolve(false); } }, t("cancel")),
        el("button", { class: `btn ${danger ? "danger" : "primary"}`, onclick: () => { close(); resolve(true); } }, confirmLabel),
      ),
    );
    close = openModal(content, { center: true, small: true });
  });
}
