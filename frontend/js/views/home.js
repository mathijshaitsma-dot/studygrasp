// Home: upload-dropzone + mappen (vakken) + recente documenten + feature-uitleg.
import { api } from "../api.js";
import { el, icon, toast, timeAgo, confirmDialog, openModal } from "../util.js";
import { t } from "../i18n.js";
import { openSearch } from "../search.js";
import { openSettings, navigate } from "../app.js";

const ACCEPT = ".pdf,.pptx,.docx,.png,.jpg,.jpeg,.webp";

export function renderHome(root) {
  const topbar = el("div", { class: "topbar" },
    el("div", { class: "brand" }, el("span", { class: "logo" }, icon("book")), "StudyCopilot"),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost", onclick: () => openSearch({ onPick: (h) => navigate(`#/doc/${h.file_hash}/study/${h.page_index}`) }) },
      icon("search", "sm"), t("search"), el("kbd", {}, "Ctrl K")),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );

  const fileInput = el("input", { type: "file", accept: ACCEPT, style: "display:none" });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) startUpload(fileInput.files[0]); });

  const dropzone = el("div", { class: "dropzone", role: "button", tabindex: "0" },
    el("div", { class: "dz-icon" }, icon("upload", "lg")),
    el("h3", {}, t("drop_title")),
    el("p", {}, t("drop_sub")),
    el("div", { class: "formats" },
      ...["PDF", "PowerPoint", "Word", t("fmt_image")].map(f => el("span", { class: "chip" }, f))),
  );
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
    const file = e.dataTransfer.files?.[0];
    if (file) startUpload(file);
  });

  const uploadArea = el("div", {}, dropzone);

  async function startUpload(file) {
    const okExt = ACCEPT.split(",").some(ext => file.name.toLowerCase().endsWith(ext));
    if (!okExt) { toast(t("err_filetype"), "err"); return; }

    const pct = el("span", { class: "pct" }, "0%");
    const fill = el("div", { class: "progress-fill", style: "width:0%" });
    const status = el("p", { style: "margin:10px 0 0;font-size:12.5px;color:var(--muted)" }, t("uploading"));
    uploadArea.replaceChildren(
      el("div", { class: "upload-progress" },
        el("div", { class: "row" }, el("span", { class: "name" }, file.name), pct),
        el("div", { class: "progress-track" }, fill),
        status,
      ),
    );

    try {
      const doc = await api.upload(file, (frac) => {
        const p = Math.round(frac * 100);
        pct.textContent = `${p}%`;
        fill.style.width = `${p}%`;
        if (frac >= 1) {
          status.textContent = t("processing");
          fill.classList.add("indeterminate");
        }
      });
      toast(t("upload_done", { name: doc.file_name, n: doc.total_pages }), "ok");
      if (doc.note) toast(doc.note, "info", 5000);
      navigate(`#/doc/${doc.file_hash}`);
    } catch (err) {
      toast(err.message, "err", 5000);
      uploadArea.replaceChildren(dropzone);
    }
  }

  const recentSection = el("div", {});
  loadRecent(recentSection);

  const home = el("div", { class: "home" },
    el("div", { class: "home-inner" },
      el("div", { class: "hero" },
        el("h1", { html: t("hero_html") }),
        el("p", {}, t("hero_sub")),
      ),
      uploadArea,
      recentSection,
      el("div", { class: "section-title" }, t("what_title"), el("span", { class: "line" })),
      el("div", { class: "feature-row" },
        feature("book", t("feat_explain_t"), t("feat_explain_b")),
        feature("crop", t("feat_region_t"), t("feat_region_b")),
        feature("quiz", t("feat_quiz_t"), t("feat_quiz_b")),
        feature("cards", t("feat_cards_t"), t("feat_cards_b")),
      ),
    ),
  );

  root.append(topbar, home);
}

function feature(iconName, title, body) {
  return el("div", { class: "feature-card" }, icon(iconName), el("h4", {}, title), el("p", {}, body));
}

async function loadRecent(container) {
  let docs, folders;
  try {
    const [docsData, foldersData] = await Promise.all([api.getDocuments(), api.folders()]);
    docs = docsData.documents || [];
    folders = foldersData.folders || [];
  } catch {
    container.replaceChildren(
      el("div", { class: "section-title" }, t("resume_title"), el("span", { class: "line" })),
      el("div", { class: "empty-state" },
        el("p", { style: "margin:0 0 4px;font-weight:600;color:var(--text)" }, t("backend_err_t")),
        el("p", { style: "margin:0;font-size:13px" }, t("backend_err_pre"), el("code", {}, "uvicorn backend:app --port 8000"), t("backend_err_post")),
      ),
    );
    return;
  }

  const refresh = () => loadRecent(container);
  const kids = [];

  /* ---------- mappen (vakken) ---------- */
  if (folders.length || docs.length) {
    const newFolderBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" },
      icon("folder-plus", "sm"), t("new_folder"));
    newFolderBtn.addEventListener("click", () => promptFolderName(refresh));

    kids.push(el("div", { class: "section-title" }, t("folders_title"), el("span", { class: "line" }), newFolderBtn));

    if (folders.length) {
      kids.push(el("div", { class: "folder-grid" },
        ...folders.map(f => el("button", { class: "folder-card", onclick: () => navigate(`#/folder/${f.id}`) },
          el("span", { class: "f-icon" }, icon("folder")),
          el("div", { style: "flex:1;min-width:0;text-align:left" },
            el("div", { class: "f-name" }, f.name),
            el("div", { class: "f-meta" }, t("folder_docs", { n: f.document_count }))),
          icon("right", "sm"),
        )),
      ));
    } else {
      kids.push(el("p", { style: "margin:0 0 8px;font-size:12.5px;color:var(--muted)" }, t("folders_hint")));
    }
  }

  /* ---------- recente documenten (zonder map) ---------- */
  const unfiled = docs.filter(d => !d.folder_id);
  if (unfiled.length) {
    const grid = el("div", { class: "doc-grid" });
    for (const d of unfiled) grid.append(docCard(d, folders, refresh));
    kids.push(
      el("div", { class: "section-title" }, t("resume_title"), el("span", { class: "line" })),
      grid,
    );
  }

  if (kids.length) container.replaceChildren(...kids);
}

function docCard(d, folders, refresh) {
  const progress = d.total_pages > 1 ? ((d.last_page_index || 0) + 1) / d.total_pages : 1;
  const thumb = d.thumbnail_url
    ? el("img", { src: api.base + d.thumbnail_url, loading: "lazy", alt: "" })
    : el("div", { class: "ph" }, icon("image", "lg"));

  const card = el("button", { class: "doc-card", onclick: () => navigate(`#/doc/${d.file_hash}`) },
    el("div", { class: "thumb" }, thumb),
    el("div", { class: "body" },
      el("div", { class: "title" }, d.file_name),
      el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${Math.round(progress * 100)}%` })),
      el("div", { class: "meta" },
        icon("clock", "sm"),
        timeAgo(d.last_opened_at || d.uploaded_at),
        el("span", { style: "margin-left:auto" }, t("slide_frac", { a: (d.last_page_index || 0) + 1, b: d.total_pages })),
      ),
    ),
  );
  // in een map zetten
  const moveBtn = el("button", { class: "del move", title: t("move_to_folder"), onclick: (e) => {
    e.stopPropagation();
    pickFolder(d, folders, refresh);
  } }, icon("folder", "sm"));
  const del = el("button", { class: "del", title: t("delete"), onclick: async (e) => {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: t("del_confirm_t"),
      body: t("del_confirm_b", { name: d.file_name }),
    });
    if (!ok) return;
    try {
      await api.deleteDocument(d.file_hash);
      card.remove();
      toast(t("deleted"), "ok");
    } catch (err) { toast(err.message, "err"); }
  } }, icon("trash", "sm"));
  card.append(moveBtn, del);
  return card;
}

// Nieuwe map aanmaken; onDone krijgt (optioneel) de nieuwe map terug.
function promptFolderName(onDone) {
  const input = el("input", { class: "field", placeholder: t("folder_name_ph"), maxlength: "80" });
  let close;
  const createBtn = el("button", { class: "btn primary", onclick: async () => {
    const name = input.value.trim();
    if (!name) return;
    try {
      const res = await api.folderCreate(name);
      close();
      toast(t("folder_created", { name }), "ok");
      onDone?.(res.folder);
    } catch (err) { toast(err.message, "err"); }
  } }, t("create"));
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" }, el("h3", {}, t("new_folder")), input),
    el("div", { class: "confirm-foot" },
      el("button", { class: "btn", onclick: () => close() }, t("cancel")), createBtn),
  ), { center: true, small: true });
  input.focus();
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") createBtn.click(); });
}

// Kies (of maak) een map voor dit document.
function pickFolder(doc, folders, refresh) {
  let close;
  const assign = async (folderId, folderName) => {
    try {
      await api.setDocumentFolder(doc.file_hash, folderId);
      close();
      toast(t("moved_to_folder", { name: folderName }), "ok");
      refresh();
    } catch (err) { toast(err.message, "err"); }
  };
  const list = el("div", { class: "folder-pick" },
    ...folders.map(f => el("button", { class: "folder-pick-row", onclick: () => assign(f.id, f.name) },
      icon("folder", "sm"), el("span", { style: "flex:1;text-align:left" }, f.name),
      el("span", { class: "chip" }, String(f.document_count)))),
    el("button", { class: "folder-pick-row new", onclick: () => {
      close();
      promptFolderName((folder) => { if (folder) assign(folder.id, folder.name); });
    } }, icon("folder-plus", "sm"), el("span", { style: "flex:1;text-align:left" }, t("new_folder"))),
  );
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" },
      el("h3", {}, t("move_to_folder")),
      el("p", { style: "margin:0 0 10px" }, doc.file_name),
      list),
    el("div", { class: "confirm-foot" },
      el("button", { class: "btn", onclick: () => close() }, t("cancel"))),
  ), { center: true, small: true });
}
