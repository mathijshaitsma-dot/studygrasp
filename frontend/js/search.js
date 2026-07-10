// Ctrl+K zoekpalet: zoekt door alle documenten (of één document) en springt
// naar de gevonden dia.
import { api } from "./api.js";
import { el, icon, debounce, escapeHtml, openModal } from "./util.js";
import { t } from "./i18n.js";

export function openSearch({ fileHash = "", onPick } = {}) {
  const input = el("input", { placeholder: fileHash ? t("search_ph_doc") : t("search_ph_all"), autofocus: true });
  const results = el("div", { class: "search-results" },
    el("div", { class: "search-empty" }, t("search_hint")));
  let hits = [];
  let sel = -1;
  let close;

  const renderHits = () => {
    results.replaceChildren();
    if (!hits.length) {
      results.append(el("div", { class: "search-empty" }, input.value.trim() ? t("no_results") : t("type_to_search")));
      return;
    }
    hits.forEach((h, i) => {
      const snippetHtml = escapeHtml(h.snippet || "").replace(
        new RegExp(`(${input.value.trim().split(/\s+/).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi"),
        "<mark>$1</mark>"
      );
      const btn = el("button", {
        class: `search-hit${i === sel ? " sel" : ""}`,
        onclick: () => { close(); onPick?.(h); },
      },
        el("img", { src: api.base + h.image_url, loading: "lazy", alt: "" }),
        el("div", { class: "info" },
          el("div", { class: "t" }, `${h.file_name} — ${h.label}`),
          el("div", { class: "s", html: snippetHtml }),
        ),
      );
      results.append(btn);
    });
  };

  const doSearch = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { hits = []; sel = -1; renderHits(); return; }
    try {
      const data = await api.search(q, fileHash);
      hits = data.results || [];
      sel = hits.length ? 0 : -1;
      renderHits();
    } catch { /* backend niet bereikbaar; leeg laten */ }
  }, 220);

  input.addEventListener("input", doSearch);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, hits.length - 1); renderHits(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); renderHits(); }
    else if (e.key === "Enter" && sel >= 0 && hits[sel]) { close(); onPick?.(hits[sel]); }
  });

  const content = el("div", { class: "grid-wrap" },
    el("div", { class: "search-head" }, icon("search"), input, el("kbd", {}, "esc")),
    results,
  );
  close = openModal(content);
  requestAnimationFrame(() => input.focus());
}
