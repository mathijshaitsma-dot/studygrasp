// Openbare landingspagina voor een geheime map-link. De bronmap blijft privé;
// pas na een bewuste import krijgt de huidige account- of gastwerkruimte eigen
// documentmetadata. Persoonlijke voortgang wordt nooit meegekopieerd.
import { api } from "../api.js";
import { el, icon, brandMark, toast } from "../util.js";
import { t } from "../i18n.js";
import { guestLoginButton, navigate } from "../app.js";

export async function renderSharedFolder(root, token) {
  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    brandMark(),
    el("div", { class: "spacer" }),
    guestLoginButton({ compact: true }),
  );
  const main = el("main", { class: "content-page shared-folder-page" },
    el("div", { style: "display:grid;place-items:center;padding:70px" }, el("div", { class: "spinner" })),
  );
  root.append(topbar, main);

  let share;
  try {
    share = (await api.folderShareGet(token)).share;
  } catch (err) {
    main.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "empty-state" },
        icon("alert", "lg"), el("h1", {}, t("shared_unavailable_title")),
        el("p", {}, err.message || t("shared_unavailable_body")),
        el("button", { class: "btn primary", onclick: () => navigate("#/") }, t("to_home")),
      )));
    return;
  }

  const accept = el("button", { class: "btn primary lg", onclick: async () => {
    accept.disabled = true;
    accept.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }), t("shared_importing"));
    try {
      const result = await api.folderShareAccept(token);
      if (result.already_present) toast(t("shared_existing_docs", { n: result.already_present }), "info", 5000);
      else toast(t("shared_imported"), "ok");
      navigate(`#/folder/${result.folder_id}`);
    } catch (err) {
      accept.disabled = false;
      accept.replaceChildren(icon("folder-plus", "sm"), t("shared_add_button"));
      toast(err.message, "err", 5000);
    }
  } }, icon("folder-plus", "sm"), t("shared_add_button"));

  const documents = share.documents || [];
  main.replaceChildren(el("div", { class: "content-inner narrow shared-folder-card" },
    el("div", { class: "shared-folder-icon" }, icon("folder", "lg")),
    el("span", { class: "chip accent" }, t("shared_folder_label")),
    el("h1", { class: "page-title" }, share.name),
    el("p", { class: "page-sub" }, t("shared_folder_summary", {
      docs: share.document_count || 0, folders: share.subfolder_count || 0,
    })),
    el("div", { class: "shared-privacy-note" },
      icon("check", "sm"), el("span", {}, t("shared_privacy_note"))),
    documents.length ? el("div", { class: "shared-document-list" },
      el("h2", {}, t("shared_contents")),
      ...documents.map(doc => el("div", { class: "shared-document-row" },
        icon("doc", "sm"), el("span", {}, doc.file_name),
        el("small", {}, t("shared_pages", { n: doc.total_pages || 0 })),
      )),
      share.document_count > documents.length
        ? el("p", { class: "page-sub" }, t("shared_more_docs", { n: share.document_count - documents.length })) : null,
    ) : el("div", { class: "empty-state" }, el("p", {}, t("shared_empty"))),
    accept,
    el("p", { class: "shared-import-explain" }, t("shared_import_explain")),
  ));
}
