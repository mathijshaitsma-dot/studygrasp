// Eén eenvoudig zoekpalet voor letterlijk zoeken én brongebonden AI-vragen.
// Typen toont direct de snelle resultaten; Enter schakelt vanzelf naar AI als
// de invoer een vraag/opdracht is of wanneer letterlijk zoeken niets vindt.
import { api } from "./api.js";
import { el, icon, debounce, escapeHtml, openModal, toast } from "./util.js";
import { t } from "./i18n.js";
import { prefs } from "./state.js";
import { renderMarkdown } from "./markdown.js";
import { stageSmartAnswer } from "./views/smartanswer.js";

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
  let indexing = false;
  let indexPolls = 0;
  let retryTimer = 0;

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
      results.append(indexing
        ? el("div", { class: "smart-search-loading" },
            el("span", { class: "spinner" }),
            el("strong", {}, t("search_indexing")),
            el("span", {}, t("search_indexing_hint")))
        : el("div", { class: "search-empty" }, t("no_results")));
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
    if (query.length >= 2 && !indexing) results.append(aiAction(query));
  };

  const searchNow = async () => {
    const query = input.value.trim();
    lastQuery = query;
    if (query.length < 2) { hits = []; sel = -1; renderHits(); return; }
    try {
      const data = await api.search(query, { fileHash, folderId });
      if (input.value.trim() !== query || aiRunning) return;
      hits = data.results || [];
      indexing = Boolean(data.indexing && !hits.length);
      sel = hits.length ? 0 : -1;
      renderHits();
      clearTimeout(retryTimer);
      if (indexing && indexPolls < 40) {
        indexPolls += 1;
        retryTimer = setTimeout(() => {
          if (input.isConnected && input.value.trim() === query && !aiRunning) searchNow();
        }, Math.min(2500, 900 + indexPolls * 100));
      }
    } catch {
      if (!aiRunning) { hits = []; indexing = false; sel = -1; renderHits(); }
    }
  };
  const doSearch = debounce(searchNow, 220);

  async function runSmartSearch(forcedQuery = "") {
    const query = (forcedQuery || input.value).trim();
    if (query.length < 2 || aiRunning) return;
    aiRunning = true;
    lastQuery = query;
    const answerId = stageSmartAnswer({ data: null, query, fileHash, folderId, scopeName: scopeLabel });
    close();
    location.hash = `#/answer/${answerId}`;
  }

  async function showSavePanel(data, query) {
    results.replaceChildren(el("div", { class: "smart-search-loading" }, el("span", { class: "spinner" })));
    let folders = [];
    try { folders = (await api.folders()).folders || []; }
    catch (err) { toast(err.message, "err"); renderAnswer(data, query); return; }

    const byId = new Map(folders.map(folder => [folder.id, folder]));
    const pathFor = (folder) => {
      const names = [], seen = new Set();
      let current = folder;
      while (current && !seen.has(current.id)) {
        seen.add(current.id); names.unshift(current.name); current = byId.get(current.parent_id);
      }
      return names.join(" › ");
    };
    const title = el("input", { class: "field", value: data.title || query, maxlength: "160" });
    const destination = el("select", { class: "field" },
      el("option", { value: "" }, t("overview_root")),
      ...folders.slice().sort((a, b) => pathFor(a).localeCompare(pathFor(b))).map(folder =>
        el("option", { value: folder.id }, pathFor(folder))),
    );
    destination.value = folderId || "";
    const saveButton = el("button", { class: "btn primary", onclick: async () => {
      const name = title.value.trim();
      if (!name) { title.focus(); return; }
      saveButton.disabled = true;
      try {
        const saved = await api.savedOverviewCreate({
          title: name, markdown: data.markdown || "", query,
          scope_label: data.scope_label || scopeLabel,
          folder_id: destination.value || null,
          citations: data.citations || [],
        });
        data.saved_overview_id = saved.overview.id;
        toast(t("overview_saved", { name }), "ok");
        renderAnswer(data, query);
      } catch (err) {
        saveButton.disabled = false;
        toast(err.message, "err", 5000);
      }
    } }, icon("check", "sm"), t("save"));
    results.replaceChildren(el("div", { class: "overview-save-panel" },
      el("div", { class: "overview-save-head" },
        el("span", { class: "overview-save-icon" }, icon("summary")),
        el("div", {}, el("h3", {}, t("overview_save_title")), el("p", {}, t("overview_save_body")))),
      el("label", {}, t("overview_name"), title),
      el("label", {}, t("overview_folder"), destination),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => renderAnswer(data, query) }, t("cancel")),
        saveButton),
    ));
    title.focus();
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
            class: "btn primary", disabled: Boolean(data.saved_overview_id),
            onclick: () => showSavePanel(data, query),
          }, icon(data.saved_overview_id ? "check" : "download", "sm"),
          data.saved_overview_id ? t("overview_saved_short") : t("save")),
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
    if (input.value.trim() !== lastQuery) {
      clearTimeout(retryTimer); hits = []; indexing = false; indexPolls = 0; sel = -1;
    }
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
      // Enter direct na typen mag geen dure AI-vraag starten alleen omdat de
      // gedebouncete letterlijke zoekactie nog niet klaar was.
      if (query !== lastQuery) { searchNow(); return; }
      if (indexing) { searchNow(); return; }
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
