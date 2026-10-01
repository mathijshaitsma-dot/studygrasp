// Mapweergave (vak): alle documenten van één vak bij elkaar, met per document
// een samenvatting-snelkoppeling en een oefententamen over de hele map.
import { api } from "../api.js";
import { el, icon, toast, timeAgo, confirmDialog, openModal } from "../util.js";
import { t } from "../i18n.js";
import { openSettings, navigate } from "../app.js";
import { mountExam } from "./exam.js";
import { mountExercisesInFolder } from "./exercises.js";
import { mountFolderSummary } from "./summary.js";
import { mountFolderFlashcards } from "./flashcards.js";
import { folderTree, promptFolderName, folderCountLabel, subfolderCountLabel } from "./home.js";

export async function renderFolder(root, folderId, sub = null) {
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);

  let folder = null, docs = [], allFolders = [], subfolders = [], crumbs = [], progress = null;
  try {
    const [foldersData, docsData, progressData] = await Promise.all([
      api.folders(), api.getDocuments(), api.folderProgress(folderId).catch(() => null),
    ]);
    allFolders = foldersData.folders || [];
    folder = allFolders.find(f => f.id === folderId);
    const studyable = (docsData.documents || []).filter(d => d.kind !== "exercise" && d.kind !== "quick");
    // Alleen wat hier rechtstreeks in zit: wat in een submap staat, zie je daar.
    docs = studyable.filter(d => d.folder_id === folderId);
    subfolders = allFolders.filter(f => f.parent_id === folderId);
    crumbs = folderCrumbs(allFolders, folderId);
    progress = progressData;
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

  // Kruimelpad: bij geneste mappen is "waar ben ik" anders niet af te lezen.
  const trail = el("nav", { class: "folder-crumbs", "aria-label": t("folder_breadcrumb") });
  crumbs.forEach((c, i) => {
    const last = i === crumbs.length - 1;
    if (i) trail.append(el("span", { class: "sep", "aria-hidden": "true" }, icon("right", "sm")));
    trail.append(last
      ? el("span", { class: "crumb current", "aria-current": "page", title: c.name }, c.name)
      : el("button", { class: "crumb", title: c.name, onclick: () => navigate(`#/folder/${c.id}`) }, c.name));
  });

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("folder")), ""),
    trail,
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );
  const main = el("div", { class: "workspace", style: "overflow:auto" });
  root.append(topbar, main);

  // Studeergereedschap over de hele map (submappen meegeteld) — dezelfde drie
  // dingen als bij een los document, maar dan over het vak.
  if (sub === "exam") {
    mountExam(main, { scope: { folder_id: folderId }, name: folder.name });
    return;
  }
  if (sub === "summary") {
    mountFolderSummary(main, folder);
    return;
  }
  if (sub === "cards") {
    mountFolderFlashcards(main, folder);
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
    const ok = await confirmDialog({
      title: t("folder_del_t"),
      body: subfolders.length ? t("folder_del_b_sub", { name: folder.name }) : t("folder_del_b", { name: folder.name }),
    });
    if (!ok) return;
    try {
      await api.folderDelete(folderId);
      toast(t("folder_deleted"), "ok");
      navigate("#/");
    } catch (err) { toast(err.message, "err"); }
  });

  // De studeerknoppen gaan over álles in dit vak, dus ook over wat in submappen
  // staat. Een map met alleen submappen is dus niet "leeg" voor deze knoppen.
  const totalDocs = folder.total_document_count ?? docs.length;
  const studyBtn = (ic, label, route) => el("button", {
    class: "btn lg", disabled: totalDocs === 0,
    onclick: () => navigate(`#/folder/${folderId}/${route}`),
  }, icon(ic, "sm"), label);

  const examBtn = el("button", { class: "btn primary lg", disabled: totalDocs === 0,
    onclick: () => navigate(`#/folder/${folderId}/exam`) },
    icon("cap", "sm"), t("folder_exam_btn"));
  const summaryBtn = studyBtn("summary", t("folder_summary_btn"), "summary");
  const cardsBtn = studyBtn("cards", t("folder_cards_btn"), "cards");

  // Documenten toevoegen gebeurt op de homepagina: daar staat alles wat nog
  // geen vak heeft al op een rij, inclusief voorbeeldplaatje en voortgang.
  const addBtn = el("button", { class: "btn", onclick: () => navigate(`#/pick/${folderId}`) },
    icon("folder-plus", "sm"), t("folder_add_docs"));

  const newSubBtn = el("button", { class: "btn ghost",
    onclick: () => promptFolderName(() => rerenderFolder(), folderId) },
    icon("folder-plus", "sm"), t("new_subfolder"));

  const moveBtn = el("button", { class: "btn ghost",
    onclick: () => openMoveFolderModal(folder, allFolders, rerenderFolder) },
    icon("folder", "sm"), t("folder_move"));

  // "Delen" werkt vandaag al: er is nog geen per-gebruiker scheiding op mappen/
  // documenten, dus wie dezelfde server bezoekt ziet al hetzelfde vak.
  const shareBtn = el("button", { class: "btn ghost", title: t("share_folder_hint"), onclick: async () => {
    const url = `${location.origin}${location.pathname}#/folder/${folderId}`;
    try {
      await navigator.clipboard.writeText(url);
      toast(t("share_folder_done"), "ok");
    } catch { toast(url, "info", 6000); }
  } }, icon("copy", "sm"), t("share_folder"));

  const inner = el("div", { class: "content-inner" },
    el("h1", { class: "page-title" }, icon("folder"), folder.name),
    el("p", { class: "page-sub" }, t("folder_sub", { n: folder.total_document_count ?? docs.length })),
    el("div", { class: "folder-tools" }, examBtn, summaryBtn, cardsBtn),
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:26px" },
      addBtn, newSubBtn, shareBtn, el("span", { class: "spacer" }), renameBtn, moveBtn, deleteBtn),
  );

  const progSection = progressSection(progress);
  if (progSection) inner.append(progSection);

  if (subfolders.length) {
    inner.append(
      el("div", { class: "section-title" }, t("subfolders_title"), el("span", { class: "line" })),
      el("div", { class: "folder-grid" },
        ...subfolders.map(f => subfolderCard(f))),
    );
  }

  if (docs.length) {
    if (subfolders.length) {
      inner.append(el("div", { class: "section-title" }, t("folder_docs_title"), el("span", { class: "line" })));
    }
    const grid = el("div", { class: "doc-grid" });
    for (const d of docs) {
      grid.append(folderDocCard(d, folderId, rerenderFolder));
    }
    inner.append(grid);
  } else if (!subfolders.length) {
    inner.append(el("div", { class: "empty-state" },
      el("p", {}, t("folder_empty")),
      el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin-top:12px" },
        el("button", { class: "btn primary", onclick: () => navigate(`#/pick/${folderId}`) },
          icon("folder-plus", "sm"), t("folder_add_docs")),
        el("button", { class: "btn", onclick: () => promptFolderName(() => rerenderFolder(), folderId) },
          icon("folder-plus", "sm"), t("new_subfolder")),
      ),
    ));
  }

  // Vak-brede opgaven (oefententamens over het hele vak).
  const exercisesSection = el("div", { style: "margin-top:30px" });
  inner.append(exercisesSection);
  mountExercisesInFolder(exercisesSection, folder);

  main.append(el("div", { class: "content-page" }, inner));
}

function statCard(num, label, color) {
  return el("div", { class: "stat-card" },
    el("div", { class: "num", style: color ? `color:${color}` : "" }, String(num)),
    el("div", { class: "lbl" }, label));
}

const BUCKET_LABEL_KEY = { due_now: "plan_due_now", tomorrow: "plan_tomorrow", this_week: "plan_week", later: "plan_later", mastered: "plan_mastered" };
const BUCKET_COLOR = { due_now: "var(--red)", tomorrow: "var(--amber)", this_week: "var(--accent)", later: "var(--muted)", mastered: "var(--green)" };

function progressSection(progress) {
  if (!progress) return null;
  const { concepts, weak_concepts, flashcards, per_document } = progress;
  const totalConcepts = Object.values(concepts || {}).reduce((a, b) => a + b, 0);
  if (!totalConcepts && !flashcards?.total) return null; // nog niets om te tonen

  const bucketsRow = el("div", { class: "fc-stats" },
    ...Object.entries(concepts).map(([key, n]) => statCard(n, t(BUCKET_LABEL_KEY[key] || key), BUCKET_COLOR[key])),
  );

  const weakList = weak_concepts?.length
    ? el("div", { class: "plan-bucket" },
        el("div", { class: "plan-bucket-head" }, el("span", { class: "plan-dot", style: "background:var(--red)" }), t("progress_weak_concepts")),
        ...weak_concepts.map(c => el("div", { class: "plan-row" },
          el("span", { style: "flex:1" }, c.concept),
          el("span", { class: "chip" }, `${Math.round(c.mastery * 100)}%`),
          (c.file_hash != null && c.page_index != null)
            ? el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => navigate(`#/doc/${c.file_hash}/study/${c.page_index}`) }, t("slide_chip", { n: c.page_index + 1 }))
            : null,
        )))
    : null;

  const fcRow = flashcards?.total
    ? el("div", { class: "fc-stats" },
        statCard(flashcards.total, t("stat_total")),
        statCard(flashcards.due_now, t("stat_due"), flashcards.due_now > 0 ? "var(--accent)" : null),
        statCard(flashcards.mastered, t("stat_learned")),
      )
    : null;

  const withMastery = (per_document || []).filter(d => d.mastery_pct != null);
  const docList = withMastery.length
    ? el("div", { class: "plan-bucket" },
        el("div", { class: "plan-bucket-head" }, t("progress_per_doc")),
        ...withMastery.map(d => el("div", { class: "plan-row" },
          el("span", { style: "flex:1" }, d.file_name),
          el("span", { class: "chip accent" }, `${d.mastery_pct}%`),
        )))
    : null;

  return el("div", { style: "margin-bottom:8px" },
    el("div", { class: "section-title" }, t("progress_title"), el("span", { class: "line" })),
    bucketsRow, weakList, fcRow, docList,
  );
}

function folderDocCard(d, folderId, rerenderFolder) {
  const lastPage = Math.max(0, Math.min(d.last_page_index || 0, Math.max(0, d.total_pages - 1)));
  const progress = d.total_pages > 1 ? (lastPage + 1) / d.total_pages : 1;
  const restart = lastPage > 0
    ? el("button", { class: "restart-chip", title: t("start_over"), onclick: (e) => {
        e.stopPropagation();
        navigate(`#/doc/${d.file_hash}/study/0`);
      } }, icon("refresh", "sm"), t("start_over"))
    : null;
  const thumb = d.thumbnail_url ? el("img", { loading: "lazy", alt: "" })
                                : el("div", { class: "ph" }, icon("image", "lg"));
  if (d.thumbnail_url) api.setImage(thumb, api.base + d.thumbnail_url).catch(() => {});
  const card = el("button", { class: "doc-card", onclick: () => navigate(`#/doc/${d.file_hash}/study/${lastPage}`) },
    el("div", { class: "thumb" },
      thumb,
      restart),
    el("div", { class: "body" },
      el("div", { class: "title" }, d.file_name),
      el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${Math.round(progress * 100)}%` })),
      el("div", { class: "meta" },
        icon("clock", "sm"), timeAgo(d.last_opened_at || d.uploaded_at),
        el("span", { style: "margin-left:auto" }, t("slide_frac", { a: lastPage + 1, b: d.total_pages })),
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


// Kruimelpad van bovenin naar deze map. Cyclusbestendig: een kapotte parent_id
// mag nooit een eindeloze wandeling worden.
function folderCrumbs(folders, folderId) {
  const byId = new Map(folders.map(f => [f.id, f]));
  const chain = [];
  const seen = new Set();
  let cur = byId.get(folderId);
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id);
    chain.push({ id: cur.id, name: cur.name });
    cur = cur.parent_id ? byId.get(cur.parent_id) : null;
  }
  return chain.reverse();
}

function subfolderCard(f) {
  const total = f.total_document_count ?? f.document_count;
  const meta = [folderCountLabel(total)];
  if (f.subfolder_count) meta.push(subfolderCountLabel(f.subfolder_count));
  return el("button", { class: "folder-card", onclick: () => navigate(`#/folder/${f.id}`) },
    el("span", { class: "f-icon" }, icon("folder")),
    el("div", { style: "flex:1;min-width:0;text-align:left" },
      el("div", { class: "f-name" }, f.name),
      el("div", { class: "f-meta" }, meta.join(" \u00b7 "))),
    icon("right", "sm"),
  );
}

// ---------- deze map in een andere map zetten ----------
// Je eigen submappen staan er bewust niet bij: een map in zijn eigen submap
// schuiven zou de hele tak van de boom losknippen (de backend weigert dat ook).
function openMoveFolderModal(folder, allFolders, onDone) {
  let close;
  const forbidden = descendantIds(allFolders, folder.id);
  const options = folderTree(allFolders).filter(f => !forbidden.has(f.id));

  const move = async (parentId) => {
    try {
      await api.folderMove(folder.id, parentId);
      close();
      toast(t("folder_moved"), "ok");
      onDone();
    } catch (err) { toast(err.message, "err"); }
  };

  const rows = options.map(f => el("button", {
    class: "folder-pick-row",
    style: f.depth ? `padding-left:${12 + f.depth * 16}px` : null,
    disabled: f.id === folder.parent_id,
    onclick: () => move(f.id),
  }, icon("folder", "sm"), el("span", { style: "flex:1;text-align:left" }, f.name)));

  const body = el("div", { class: "folder-pick" },
    el("button", { class: "folder-pick-row", disabled: !folder.parent_id, onclick: () => move(null) },
      icon("home", "sm"), el("span", { style: "flex:1;text-align:left" }, t("folder_move_root"))),
    ...rows,
  );

  close = openModal(el("div", {},
    el("div", { class: "confirm-body" },
      el("h3", {}, t("folder_move")),
      el("p", { style: "margin-bottom:12px" }, t("folder_move_sub", { name: folder.name })),
      body),
    el("div", { class: "confirm-foot" },
      el("button", { class: "btn", onclick: () => close() }, t("cancel"))),
  ), { center: true, label: t("folder_move") });
}

function descendantIds(folders, rootId) {
  const out = new Set([rootId]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const f of folders) {
      if (f.parent_id && out.has(f.parent_id) && !out.has(f.id)) {
        out.add(f.id);
        grew = true;
      }
    }
  }
  return out;
}
