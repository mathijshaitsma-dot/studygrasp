// Opgeslagen AI-uitleg/overzicht. Dit is een eigen lichtgewicht studie-artefact
// dat in de hoofdmap, een vakmap of een submap kan staan.
import { api } from "../api.js";
import { el, icon, brandMark, toast, confirmDialog, openModal } from "../util.js";
import { renderMarkdown } from "../markdown.js";
import { t } from "../i18n.js";
import { navigate, openSettings } from "../app.js";

function folderOptions(folders, selected = "") {
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

export async function renderSavedOverview(root, overviewId) {
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);
  let overview, folders = [];
  try {
    const [overviewData, foldersData] = await Promise.all([api.savedOverviewGet(overviewId), api.folders()]);
    overview = overviewData.overview;
    folders = foldersData.folders || [];
  } catch (err) {
    loading.remove();
    root.append(el("div", { class: "empty-state", style: "margin:auto" },
      el("p", {}, err.message),
      el("button", { class: "btn primary", onclick: () => navigate("#/") }, t("to_home"))));
    return;
  }
  loading.remove();

  const goBack = () => navigate(overview.folder_id ? `#/folder/${overview.folder_id}` : "#/");
  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("back_overview"), onclick: goBack }, icon("left")),
    brandMark(false),
    el("div", { class: "doc-name", title: overview.title }, overview.title),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );

  const edit = () => {
    const name = el("input", { class: "field", value: overview.title, maxlength: "160" });
    const destination = folderOptions(folders, overview.folder_id);
    let close;
    const save = el("button", { class: "btn primary", onclick: async () => {
      const title = name.value.trim();
      if (!title) { name.focus(); return; }
      save.disabled = true;
      try {
        const data = await api.savedOverviewUpdate(overview.id, {
          title, folder_id: destination.value || null,
        });
        overview = data.overview;
        close();
        root.replaceChildren();
        renderSavedOverview(root, overview.id);
      } catch (err) { save.disabled = false; toast(err.message, "err"); }
    } }, t("save"));
    close = openModal(el("div", {},
      el("div", { class: "confirm-body" },
        el("h3", {}, t("overview_edit")),
        el("label", {}, t("overview_name"), name),
        el("label", {}, t("overview_folder"), destination)),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => close() }, t("cancel")), save)),
      { center: true, small: true });
    name.focus();
  };

  const remove = async () => {
    if (!await confirmDialog({ title: t("overview_delete_title"), body: t("overview_delete_body"), danger: true })) return;
    try { await api.savedOverviewDelete(overview.id); toast(t("overview_deleted"), "ok"); goBack(); }
    catch (err) { toast(err.message, "err"); }
  };

  const sources = (overview.citations || []).map(citation => {
    const image = el("img", { loading: "lazy", alt: "" });
    if (citation.image_url) api.setImage(image, api.base + citation.image_url).catch(() => {});
    return el("button", { class: "smart-source", onclick: () => navigate(`#/doc/${citation.file_hash}/study/${citation.page_index}`) },
      image,
      el("span", {},
        el("strong", {}, `${citation.file_name} — ${citation.label}`),
        citation.why ? el("small", {}, citation.why) : null),
      icon("right", "sm"));
  });

  const content = el("div", { class: "content-page overview-page" }, el("div", { class: "content-inner narrow" },
    el("div", { class: "overview-kicker" }, icon("summary", "sm"), t("saved_overview")),
    el("h1", { class: "page-title" }, overview.title),
    overview.query ? el("p", { class: "page-sub" }, t("overview_question", { q: overview.query })) : null,
    el("div", { class: "answer-meta no-print" },
      overview.scope_label ? el("span", { class: "chip" }, overview.scope_label) : null,
      el("span", { class: "spacer" }),
      el("button", { class: "btn ghost", onclick: edit }, icon("pencil", "sm"), t("overview_edit")),
      el("button", { class: "btn ghost", onclick: remove }, icon("trash", "sm"), t("delete"))),
    el("div", { class: "md overview-markdown", html: renderMarkdown(overview.markdown || "") }),
    sources.length ? el("div", { class: "smart-sources" },
      el("div", { class: "smart-sources-title" }, t("smart_sources")), ...sources) : null,
  ));
  root.append(topbar, el("div", { class: "workspace", style: "overflow:auto" }, content));
}
