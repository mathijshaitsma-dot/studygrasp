// Mapweergave (vak): alle documenten van één vak bij elkaar, met per document
// een samenvatting-snelkoppeling en een oefententamen over de hele map.
import { api } from "../api.js";
import { el, icon, toast, timeAgo, confirmDialog, openModal } from "../util.js";
import { t } from "../i18n.js";
import { openSettings, navigate } from "../app.js";
import { mountExam } from "./exam.js";

export async function renderFolder(root, folderId, sub = null) {
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);

  let folder = null, docs = [];
  try {
    const [foldersData, docsData] = await Promise.all([api.folders(), api.getDocuments()]);
    folder = (foldersData.folders || []).find(f => f.id === folderId);
    docs = (docsData.documents || []).filter(d => d.folder_id === folderId);
  } catch (err) {
    loading.remove();
    root.append(el("div", { style: "flex:1;display:grid;place-items:center" },
      el("div", { class: "empty-state" }, el("p", {}, err.message))));
    return;
  }
  loading.remove();

  if (!folder) {
    root.append(el("div", { style: "flex:1;display:grid;place-items:center;padding:24px" },
      el("div", { class: "empty-state", style: "max-width:420px" },
        el("p", { style: "font-weight:600;color:var(--text)" }, t("folder_not_found")),
        el("button", { class: "btn primary", onclick: () => navigate("#/") }, icon("home", "sm"), t("to_home")),
      )));
    return;
  }

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("folder")), ""),
    el("div", { class: "doc-name", title: folder.name }, folder.name),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );
  const main = el("div", { class: "workspace", style: "overflow:auto" });
  root.append(topbar, main);

  // #/folder/{id}/exam => direct de tentamenmodus over de hele map
  if (sub === "exam") {
    mountExam(main, { scope: { folder_id: folderId }, name: folder.name });
    return;
  }

  const rerenderFolder = () => { root.replaceChildren(); renderFolder(root, folderId); };

  const renameBtn = el("button", { class: "btn ghost" }, icon("pencil", "sm"), t("rename"));
  renameBtn.addEventListener("click", () => {
    const input = el("input", { class: "field", value: folder.name, maxlength: "80" });
    let close;
    const saveBtn = el("button", { class: "btn primary", onclick: async () => {
      const name = input.value.trim();
      if (!name) return;
      try {
        await api.folderRename(folderId, name);
        close();
        rerenderFolder();
      } catch (err) { toast(err.message, "err"); }
    } }, t("save"));
    close = openModal(el("div", {},
      el("div", { class: "confirm-body" }, el("h3", {}, t("rename_folder")), input),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => close() }, t("cancel")), saveBtn),
    ), { center: true, small: true });
    input.focus();
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveBtn.click(); });
  });

  const deleteBtn = el("button", { class: "btn ghost" }, icon("trash", "sm"), t("delete"));
  deleteBtn.addEventListener("click", async () => {
    const ok = await confirmDialog({ title: t("folder_del_t"), body: t("folder_del_b", { name: folder.name }) });
    if (!ok) return;
    try {
      await api.folderDelete(folderId);
      toast(t("folder_deleted"), "ok");
      navigate("#/");
    } catch (err) { toast(err.message, "err"); }
  });

  const examBtn = el("button", { class: "btn primary lg", disabled: docs.length === 0,
    onclick: () => navigate(`#/folder/${folderId}/exam`) },
    icon("cap", "sm"), t("folder_exam_btn"));

  const inner = el("div", { class: "content-inner" },
    el("h1", { class: "page-title" }, icon("folder"), folder.name),
    el("p", { class: "page-sub" }, t("folder_sub", { n: docs.length })),
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:26px" },
      examBtn, el("span", { class: "spacer" }), renameBtn, deleteBtn),
  );

  if (!docs.length) {
    inner.append(el("div", { class: "empty-state" }, el("p", {}, t("folder_empty"))));
  } else {
    const grid = el("div", { class: "doc-grid" });
    for (const d of docs) {
      grid.append(folderDocCard(d, folderId, rerenderFolder));
    }
    inner.append(grid);
  }

  main.append(el("div", { class: "content-page" }, inner));
}

function folderDocCard(d, folderId, rerenderFolder) {
  const progress = d.total_pages > 1 ? ((d.last_page_index || 0) + 1) / d.total_pages : 1;
  const card = el("button", { class: "doc-card", onclick: () => navigate(`#/doc/${d.file_hash}`) },
    el("div", { class: "thumb" },
      d.thumbnail_url ? el("img", { src: api.base + d.thumbnail_url, loading: "lazy", alt: "" })
                      : el("div", { class: "ph" }, icon("image", "lg"))),
    el("div", { class: "body" },
      el("div", { class: "title" }, d.file_name),
      el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${Math.round(progress * 100)}%` })),
      el("div", { class: "meta" },
        icon("clock", "sm"), timeAgo(d.last_opened_at || d.uploaded_at),
        el("span", { style: "margin-left:auto" }, t("slide_frac", { a: (d.last_page_index || 0) + 1, b: d.total_pages })),
      ),
      // Snelkoppelingen: samenvatting per document is één klik.
      el("div", { class: "card-actions" },
        el("button", { class: "btn ghost", onclick: (e) => { e.stopPropagation(); navigate(`#/doc/${d.file_hash}/summary`); } },
          icon("summary", "sm"), t("tab_summary")),
        el("button", { class: "btn ghost", onclick: (e) => { e.stopPropagation(); navigate(`#/doc/${d.file_hash}/quiz`); } },
          icon("quiz", "sm"), t("tab_quiz")),
      ),
    ),
  );
  const removeBtn = el("button", { class: "del", title: t("remove_from_folder"), onclick: async (e) => {
    e.stopPropagation();
    try {
      await api.setDocumentFolder(d.file_hash, null);
      toast(t("removed_from_folder"), "ok");
      rerenderFolder();
    } catch (err) { toast(err.message, "err"); }
  } }, icon("x", "sm"));
  card.append(removeBtn);
  return card;
}
