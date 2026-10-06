// Eén eenvoudig zoekpalet voor letterlijk zoeken én brongebonden AI-vragen.
// Typen toont direct de snelle resultaten; Enter schakelt vanzelf naar AI als
// de invoer een vraag/opdracht is of wanneer letterlijk zoeken niets vindt.
import { api } from "./api.js";
import { el, icon, debounce, escapeHtml, openModal, toast } from "./util.js";
import { t } from "./i18n.js";
import { prefs } from "./state.js";
import { renderMarkdown } from "./markdown.js";

const AI_QUERY_RE = /[?]|\b(waar|wat|welke|hoe|waarom|maak|geef|vat|samenvat|overzicht|begrippen|formules?|casussen?|ziektes?|hoofdstuk|find|where|what|which|how|why|create|summari[sz]e|list)\b/i;

export function openSearch({ fileHash = "", folderId = "", scopeName = "", onPick, onHover,
  initialQuery = "", autoAsk = false } = {}) {
  const scopeLabel = scopeName || (fileHash ? t("smart_scope_document") : folderId ? t("smart_scope_folder") : t("smart_scope_all"));
  const input = el("input", {
    placeholder: t("smart_search_ph"), autofocus: true,
    "aria-label": t("smart_search_ph"),
  });
  const results = el("div", { class: "search-results" });
  const scope = el("span", { class: "search-scope", title: scopeLabel }, scopeLabel);
  let hits = [];
  let sel = -1;
  let close;
  let aiRunning = false;
  let lastQuery = "";

  const pick = (hit) => {
    close();
    onPick?.(hit);
  };

  const previewableThumb = (hit, row, compact = false) => {
    const thumb = el("img", { loading: "lazy", alt: t("smart_preview_slide") });
    const full = el("img", { loading: "lazy", alt: `${hit.file_name} — ${hit.label}` });
    api.setImage(thumb, api.base + hit.image_url).catch(() => {});
    api.setImage(full, api.base + hit.image_url).catch(() => {});
    const preview = el("button", { class: "search-inline-preview", hidden: true,
      title: t("smart_preview_close"), "aria-label": t("smart_preview_close") },
      full,
      el("span", {}, t("smart_preview_close")),
    );
    const button = el("button", {
      class: `search-thumb-button${compact ? " compact" : ""}`,
      title: t("smart_preview_slide"), "aria-label": t("smart_preview_slide"),
      onclick: (event) => {
        event.stopPropagation();
        const willOpen = preview.hidden;
        preview.hidden = !willOpen;
        row.classList.toggle("preview-open", willOpen);
        button.setAttribute("aria-expanded", String(willOpen));
      },
    }, thumb);
    preview.addEventListener("click", () => {
      preview.hidden = true;
      row.classList.remove("preview-open");
      button.setAttribute("aria-expanded", "false");
      button.focus();
    });
    row.append(preview);
    return button;
  };

  const aiAction = (query) => el("button", {
    class: "search-ai-action", onclick: () => runSmartSearch(query),
  },
    el("span", { class: "search-ai-action-icon" }, icon("search", "sm")),
    el("span", { class: "search-ai-action-copy" },
      el("strong", {}, t("smart_ask_material")),
      el("span", {}, t("smart_ask_query", { q: query, scope: scopeLabel }))),
    icon("right", "sm"),
  );

  const renderHits = () => {
    results.replaceChildren();
    const query = input.value.trim();
    if (!query) {
      results.append(
        el("div", { class: "search-empty smart-search-intro" },
          el("strong", {}, t("smart_search_intro")),
          el("span", {}, t("smart_search_examples"))),
      );
      return;
    }
    if (!hits.length) {
      results.append(el("div", { class: "search-empty" }, t("no_results")));
    } else {
      hits.forEach((hit, index) => {
        const words = query.split(/\s+/).filter(Boolean).map(word => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
        const snippetHtml = words.length
          ? escapeHtml(hit.snippet || "").replace(new RegExp(`(${words.join("|")})`, "gi"), "<mark>$1</mark>")
          : escapeHtml(hit.snippet || "");
        const row = el("div", { class: `search-hit${index === sel ? " sel" : ""}` });
        const openButton = el("button", { class: "search-hit-open", onclick: () => pick(hit) },
          el("div", { class: "info" },
            el("div", { class: "t" }, `${hit.file_name} — ${hit.label}`),
            el("div", { class: "s", html: snippetHtml })),
          icon("right", "sm"),
        );
        row.prepend(previewableThumb(hit, row), openButton);
        if (onHover) {
          let timer = 0;
          row.addEventListener("mouseenter", () => {
            clearTimeout(timer);
            timer = setTimeout(() => onHover(hit), 250);
          });
          row.addEventListener("mouseleave", () => clearTimeout(timer));
        }
        results.append(row);
      });
    }
    if (query.length >= 2) results.append(aiAction(query));
  };

  const doSearch = debounce(async () => {
    const query = input.value.trim();
    lastQuery = query;
    if (query.length < 2) { hits = []; sel = -1; renderHits(); return; }
    try {
      const data = await api.search(query, { fileHash, folderId });
      if (input.value.trim() !== query || aiRunning) return;
      hits = data.results || [];
      sel = hits.length ? 0 : -1;
      renderHits();
    } catch {
      if (!aiRunning) { hits = []; sel = -1; renderHits(); }
    }
  }, 220);

  async function runSmartSearch(forcedQuery = "") {
    const query = (forcedQuery || input.value).trim();
    if (query.length < 2 || aiRunning) return;
    aiRunning = true;
    lastQuery = query;
    input.disabled = true;
    results.replaceChildren(el("div", { class: "smart-search-loading" },
      el("span", { class: "spinner" }),
      el("strong", {}, t("smart_searching")),
      el("span", {}, t("smart_searching_scope", { scope: scopeLabel }))));
    try {
      const data = await api.smartSearch({
        query, file_hash: fileHash || null, folder_id: folderId || null,
        language: prefs.language,
      });
      renderAnswer(data, query);
    } catch (err) {
      results.replaceChildren(el("div", { class: "smart-search-error" },
        icon("alert"),
        el("strong", {}, t("smart_failed")),
        el("span", {}, err.message),
        el("button", { class: "btn", onclick: () => runSmartSearch(query) }, icon("refresh", "sm"), t("retry"))));
    } finally {
      aiRunning = false;
      input.disabled = false;
      input.focus();
    }
  }

  function renderAnswer(data, query) {
    const citations = data.citations || [];
    const wordlistCards = data.wordlist_cards || [];
    const coverage = t("smart_coverage", { docs: data.documents_scanned || 0, pages: data.pages_scanned || 0 });
    const saveWordlist = wordlistCards.length ? el("button", {
      class: "btn primary", onclick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const created = await api.wordlistCreate({ name: data.title || t("wordlists_title"), cards: wordlistCards });
          close();
          location.hash = `#/wordlist/${created.wordlist.id}`;
        } catch (err) {
          button.disabled = false;
          toast(err.message, "err", 5000);
        }
      },
    }, icon("book", "sm"), t("smart_save_wordlist")) : null;
    const answer = el("div", { class: "smart-answer" },
      el("div", { class: "smart-answer-head" },
        el("div", {},
          el("span", { class: "smart-answer-scope" }, data.scope_label || scopeLabel),
          el("h3", {}, data.title || t("smart_answer"))),
        el("div", { class: "smart-answer-actions" },
          saveWordlist,
          el("button", {
            class: "btn ghost", title: t("copy"),
            onclick: () => navigator.clipboard.writeText(data.markdown || "").then(() => toast(t("summary_copied"), "ok")),
          }, icon("copy", "sm"), t("copy")))),
      el("div", { class: "md smart-answer-body", html: renderMarkdown(data.markdown || "") }),
      citations.length ? el("div", { class: "smart-sources" },
        el("div", { class: "smart-sources-title" }, t("smart_sources")),
        ...citations.map(citation => {
          const row = el("div", { class: "smart-source" });
          const openButton = el("button", { class: "smart-source-open", onclick: () => pick(citation) },
            el("span", {},
              el("strong", {}, `${citation.file_name} — ${citation.label}`),
              citation.why ? el("small", {}, citation.why) : null),
            icon("right", "sm"));
          row.prepend(previewableThumb(citation, row, true), openButton);
          return row;
        })) : null,
      el("div", { class: "smart-answer-foot" },
        el("span", {}, coverage),
        el("button", { class: "btn ghost", onclick: () => {
          input.value = query;
          renderHits();
        } }, icon("left", "sm"), t("smart_back_results"))),
    );
    results.replaceChildren(answer);
  }

  input.addEventListener("input", () => {
    if (aiRunning) return;
    if (input.value.trim() !== lastQuery) { hits = []; sel = -1; }
    doSearch();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault(); sel = Math.min(sel + 1, hits.length - 1); renderHits();
    } else if (event.key === "ArrowUp") {
      event.preventDefault(); sel = Math.max(sel - 1, 0); renderHits();
    } else if (event.key === "Enter") {
      event.preventDefault();
      const query = input.value.trim();
      if (event.ctrlKey || event.metaKey || AI_QUERY_RE.test(query) || !hits.length) runSmartSearch(query);
      else if (sel >= 0 && hits[sel]) pick(hits[sel]);
    }
  });

  const content = el("div", { class: "grid-wrap smart-search-wrap" },
    el("div", { class: "search-head" }, icon("search"), input, scope, el("kbd", {}, "esc")),
    results,
  );
  close = openModal(content, { label: t("smart_search_ph") });
  input.value = initialQuery;
  renderHits();
  requestAnimationFrame(() => {
    input.focus();
    if (autoAsk && initialQuery.trim().length >= 2) runSmartSearch(initialQuery);
    else if (initialQuery.trim().length >= 2) doSearch();
  });
}
