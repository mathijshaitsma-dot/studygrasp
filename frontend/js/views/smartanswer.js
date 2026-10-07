// Volledige antwoordpagina voor vragen over een document, map of de hele
// bibliotheek. De zoekmodal blijft hierdoor een snelle ingang; lange uitleg en
// brongebonden vervolgvragen krijgen dezelfde rustige ruimte als samenvattingen.
import { api } from "../api.js";
import { el, icon, brandMark, openModal, toast } from "../util.js";
import { renderMarkdown } from "../markdown.js";
import { downloadText } from "../export.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { navigate, openSettings } from "../app.js";

const STORAGE_PREFIX = "studygrasp:smart-answer:";

function storageKey(id) { return `${STORAGE_PREFIX}${id}`; }

function persist(id, state) {
  try { sessionStorage.setItem(storageKey(id), JSON.stringify(state)); }
  catch { /* Het antwoord blijft in het huidige scherm bruikbaar. */ }
}

export function stageSmartAnswer({ data, query, fileHash = "", folderId = "", scopeName = "" }) {
  const id = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  persist(id, {
    query, data, fileHash, folderId, scopeName,
    returnHash: location.hash || "#/", followups: [], createdAt: Date.now(),
  });
  return id;
}

function loadState(id) {
  try { return JSON.parse(sessionStorage.getItem(storageKey(id)) || "null"); }
  catch { return null; }
}

function folderSelect(folders, selected = "") {
  const byId = new Map(folders.map(folder => [folder.id, folder]));
  const pathFor = (folder) => {
    const names = [], seen = new Set();
    let current = folder;
    while (current && !seen.has(current.id)) {
      seen.add(current.id); names.unshift(current.name); current = byId.get(current.parent_id);
    }
    return names.join(" › ");
  };
  const select = el("select", { class: "field" },
    el("option", { value: "" }, t("overview_root")),
    ...folders.slice().sort((a, b) => pathFor(a).localeCompare(pathFor(b))).map(folder =>
      el("option", { value: folder.id }, pathFor(folder))),
  );
  select.value = selected || "";
  return select;
}

function sourceCards(citations = []) {
  if (!citations.length) return null;
  return el("div", { class: "smart-sources" },
    el("div", { class: "smart-sources-title" }, t("smart_sources")),
    ...citations.map(citation => {
      const thumb = el("img", { loading: "lazy", alt: "" });
      if (citation.image_url) api.setImage(thumb, api.base + citation.image_url).catch(() => {});
      const preview = () => {
        const full = el("img", { class: "smart-answer-preview-image", alt: `${citation.file_name} — ${citation.label}` });
        if (citation.image_url) api.setImage(full, api.base + citation.image_url).catch(() => {});
        let close;
        const panel = el("button", { class: "smart-answer-preview", onclick: () => close?.() }, full,
          el("span", {}, t("smart_preview_close")));
        close = openModal(panel, { label: t("smart_preview_slide") });
      };
      return el("div", { class: "smart-source smart-answer-source" },
        el("button", { class: "smart-source-thumb", title: t("smart_preview_slide"), onclick: preview }, thumb),
        el("button", {
          class: "smart-source-open",
          onclick: () => navigate(`#/doc/${citation.file_hash}/study/${citation.page_index}`),
        }, el("span", {},
          el("strong", {}, `${citation.file_name} — ${citation.label}`),
          citation.why ? el("small", {}, citation.why) : null), icon("right", "sm")));
    }),
  );
}

export function renderSmartAnswer(root, answerId) {
  const state = loadState(answerId);
  if (!state) {
    root.append(el("div", { class: "empty-state", style: "margin:auto" },
      el("p", {}, t("smart_answer_expired")),
      el("button", { class: "btn primary", onclick: () => navigate("#/") }, t("to_home"))));
    return;
  }

  const goBack = () => navigate(state.returnHash || "#/");
  if (!state.data) {
    const topbar = el("div", { class: "topbar" },
      el("button", { class: "btn ghost icon-btn", title: t("back_overview"), onclick: goBack }, icon("left")),
      brandMark(false),
      el("div", { class: "doc-name" }, state.scopeName || t("smart_answer")),
      el("div", { class: "spacer" }),
      el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
    );
    const status = el("div", { class: "stream-status" },
      el("span", { class: "dots" }, el("i"), el("i"), el("i")),
      t("smart_searching_scope", { scope: state.scopeName || t("smart_scope_all") }));
    const page = el("div", { class: "content-page" }, el("div", { class: "content-inner narrow smart-answer-page" },
      el("div", { class: "overview-kicker" }, icon("search", "sm"), t("smart_answer")),
      el("h1", { class: "page-title" }, state.query),
      el("p", { class: "page-sub" }, t("smart_searching")), status));
    root.append(topbar, el("div", { class: "workspace", style: "overflow:auto" }, page));

    const generate = async () => {
      status.replaceChildren(
        el("span", { class: "dots" }, el("i"), el("i"), el("i")),
        t("smart_searching_scope", { scope: state.scopeName || t("smart_scope_all") }));
      try {
        state.data = await api.smartSearch({
          query: state.query, file_hash: state.fileHash || null, folder_id: state.folderId || null,
          language: prefs.language,
        });
        persist(answerId, state);
        root.replaceChildren();
        renderSmartAnswer(root, answerId);
      } catch (err) {
        status.replaceChildren(el("div", { class: "md-error" }, icon("alert"),
          el("div", {},
            el("strong", {}, t("smart_failed")),
            el("div", { style: "color:var(--text-soft);margin-top:4px" }, err.message),
            el("button", { class: "btn", style: "margin-top:10px", onclick: generate }, icon("refresh", "sm"), t("retry")))));
      }
    };
    generate();
    return;
  }

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("back_overview"), onclick: goBack }, icon("left")),
    brandMark(false),
    el("div", { class: "doc-name", title: state.data.title }, state.data.title || t("smart_answer")),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );

  const content = el("div", { class: "content-inner narrow smart-answer-page" });
  const main = el("div", { class: "workspace", style: "overflow:auto" },
    el("div", { class: "content-page" }, content));
  root.append(topbar, main);

  const saveOverview = async () => {
    let folders;
    try { folders = (await api.folders()).folders || []; }
    catch (err) { toast(err.message, "err"); return; }
    const name = el("input", { class: "field", value: state.data.title || state.query, maxlength: "160" });
    const destination = folderSelect(folders, state.folderId);
    let close;
    const save = el("button", { class: "btn primary", onclick: async () => {
      const title = name.value.trim();
      if (!title) { name.focus(); return; }
      save.disabled = true;
      try {
        const saved = await api.savedOverviewCreate({
          title, markdown: state.data.markdown || "", query: state.query,
          scope_label: state.data.scope_label || state.scopeName,
          folder_id: destination.value || null, citations: state.data.citations || [],
        });
        state.data.saved_overview_id = saved.overview.id;
        persist(answerId, state);
        close(); toast(t("overview_saved", { name: title }), "ok");
      } catch (err) { save.disabled = false; toast(err.message, "err", 5000); }
    } }, icon("check", "sm"), t("save"));
    close = openModal(el("div", {},
      el("div", { class: "confirm-body" },
        el("h3", {}, t("overview_save_title")),
        el("p", {}, t("overview_save_body")),
        el("label", {}, t("overview_name"), name),
        el("label", {}, t("overview_folder"), destination)),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => close() }, t("cancel")), save)),
      { center: true, small: true });
    name.focus();
  };

  const saveWordlist = async (button) => {
    button.disabled = true;
    try {
      const created = await api.wordlistCreate({
        name: state.data.title || t("wordlists_title"), cards: state.data.wordlist_cards || [],
      });
      navigate(`#/wordlist/${created.wordlist.id}`);
    } catch (err) { button.disabled = false; toast(err.message, "err", 5000); }
  };

  const actions = el("div", { class: "answer-meta no-print" },
    state.data.scope_label ? el("span", { class: "chip" }, state.data.scope_label) : null,
    el("span", { class: "spacer" }),
  );
  if ((state.data.wordlist_cards || []).length) {
    const button = el("button", { class: "btn primary", onclick: () => saveWordlist(button) },
      icon("book", "sm"), t("smart_save_wordlist"));
    actions.append(button);
  }
  actions.append(
    el("button", { class: "btn ghost", onclick: saveOverview }, icon("download", "sm"), t("save")),
    el("button", { class: "btn ghost", onclick: () => navigator.clipboard.writeText(state.data.markdown || "").then(() => toast(t("summary_copied"), "ok")) }, icon("copy", "sm"), t("copy")),
    el("button", { class: "btn ghost", onclick: () => {
      downloadText(`${state.data.title || "StudyGrasp"}.md`, "text/markdown", state.data.markdown || "");
      toast(t("download_done"), "ok");
    } }, icon("download", "sm"), t("download")),
    el("button", { class: "btn ghost", onclick: () => window.print() }, icon("printer", "sm"), t("export_print")),
  );

  content.append(
    el("div", { class: "overview-kicker" }, icon("search", "sm"), t("smart_answer")),
    el("h1", { class: "page-title" }, state.data.title || t("smart_answer")),
    el("p", { class: "page-sub" }, t("overview_question", { q: state.query })),
    actions,
    el("div", { class: "md overview-markdown", html: renderMarkdown(state.data.markdown || "") }),
    sourceCards(state.data.citations || []),
  );

  const thread = el("div", { class: "smart-followup-thread" });
  const input = el("textarea", {
    rows: "2", placeholder: t("smart_followup_ph"), "aria-label": t("smart_followup_ph"),
  });
  const send = el("button", { class: "btn primary" }, icon("right", "sm"), t("smart_followup_send"));
  const chatStatus = el("div", { class: "smart-followup-status", "aria-live": "polite" });

  const renderTurn = (turn) => {
    thread.append(
      el("div", { class: "smart-chat-user" }, turn.query),
      el("div", { class: "smart-chat-assistant" },
        el("div", { class: "md", html: renderMarkdown(turn.data.markdown || "") }),
        sourceCards(turn.data.citations || [])),
    );
  };
  (state.followups || []).forEach(renderTurn);

  const conversationHistory = () => {
    const history = [
      { role: "user", content: state.query },
      { role: "assistant", content: state.data.markdown || "" },
    ];
    for (const turn of state.followups || []) {
      history.push({ role: "user", content: turn.query });
      history.push({ role: "assistant", content: turn.data.markdown || "" });
    }
    return history.slice(-8);
  };

  const ask = async () => {
    const query = input.value.trim();
    if (query.length < 2 || send.disabled) { input.focus(); return; }
    input.value = ""; input.disabled = true; send.disabled = true;
    chatStatus.replaceChildren(el("span", { class: "spinner" }), t("smart_followup_busy"));
    const pendingUser = el("div", { class: "smart-chat-user" }, query);
    thread.append(pendingUser);
    try {
      const data = await api.smartSearch({
        query, file_hash: state.fileHash || null, folder_id: state.folderId || null,
        language: prefs.language, history: conversationHistory(),
      });
      state.followups = [...(state.followups || []), { query, data }];
      persist(answerId, state);
      thread.append(el("div", { class: "smart-chat-assistant" },
        el("div", { class: "md", html: renderMarkdown(data.markdown || "") }),
        sourceCards(data.citations || [])));
      thread.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      pendingUser.remove(); input.value = query;
      toast(err.message, "err", 5000);
    } finally {
      input.disabled = false; send.disabled = false; chatStatus.replaceChildren(); input.focus();
    }
  };
  send.addEventListener("click", ask);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(); }
  });

  content.append(el("section", { class: "smart-followup no-print" },
    el("div", { class: "smart-followup-heading" },
      el("span", { class: "smart-followup-icon" }, icon("send", "sm")),
      el("div", {}, el("h2", {}, t("smart_followup_title")), el("p", {}, t("smart_followup_body")))),
    thread,
    el("div", { class: "smart-followup-composer" }, input, send),
    chatStatus,
  ));
}
