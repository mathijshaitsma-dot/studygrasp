// Home: upload-dropzone + mappen (vakken) + recente documenten + feature-uitleg.
import { api } from "../api.js";
import { el, icon, brandMark, toast, timeAgo, confirmDialog, openModal } from "../util.js";
import { t, tList } from "../i18n.js";
import { openSearch } from "../search.js";
import { openSettings, navigate } from "../app.js";

const ACCEPT = ".pdf,.ppt,.pptx,.docx,.png,.jpg,.jpeg,.webp";

// Menu "Woordenlijst maken": zelf typen, uit een foto, of uit een document.
function openWordlistMenu() {
  let close;
  const choose = (fn) => { close(); fn(); };
  const row = (ic, label, fn) => el("button", { class: "folder-pick-row", onclick: () => choose(fn) },
    icon(ic, "sm"), el("span", { style: "flex:1;text-align:left" }, label));
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" },
      el("h3", {}, t("wordlist_action")),
      el("div", { class: "folder-pick", style: "margin-top:10px" },
        row("pencil", t("wordlist_new_typed"), wordlistFromTyping),
        row("image", t("wordlist_new_photo"), wordlistFromPhoto),
        row("doc", t("wordlist_new_doc"), wordlistFromDocument))),
    el("div", { class: "confirm-foot" }, el("button", { class: "btn", onclick: () => close() }, t("cancel")))),
    { center: true, small: true });
}

function wordlistFromTyping() {
  const input = el("input", { class: "field", placeholder: t("wordlist_name_ph"), maxlength: "120" });
  let close;
  const createBtn = el("button", { class: "btn primary", onclick: async () => {
    const name = input.value.trim();
    if (!name) return;
    try { const res = await api.wordlistCreate({ name, cards: [] }); close(); navigate(`#/wordlist/${res.wordlist.id}`); }
    catch (err) { toast(err.message, "err"); }
  } }, t("create"));
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" }, el("h3", {}, t("wordlist_new_typed")), input),
    el("div", { class: "confirm-foot" }, el("button", { class: "btn", onclick: () => close() }, t("cancel")), createBtn)),
    { center: true, small: true });
  input.focus();
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") createBtn.click(); });
}

function wordlistFromPhoto() {
  const inp = el("input", { type: "file", accept: "image/*", capture: "environment", style: "display:none" });
  document.body.appendChild(inp);
  inp.addEventListener("change", async () => {
    const file = inp.files[0];
    inp.remove();
    if (!file) return;
    toast(t("wordlist_gen_busy"), "info", 60000);
    try {
      const doc = await api.upload(file, null, "quick");
      const res = await api.wordlistGenerate({ file_hash: doc.file_hash });
      navigate(`#/wordlist/${res.wordlist.id}`);
    } catch (err) { toast(err.message, "err", 5000); }
  });
  inp.click();
}

async function wordlistFromDocument() {
  let docs = [];
  try { docs = (await api.getDocuments()).documents || []; } catch (err) { toast(err.message, "err"); return; }
  docs = docs.filter(d => d.kind !== "quick" && d.kind !== "exercise");
  if (!docs.length) { toast(t("folder_empty"), "info"); return; }
  let close;
  const pick = async (hash) => {
    close();
    toast(t("wordlist_gen_busy"), "info", 60000);
    try { const res = await api.wordlistGenerate({ file_hash: hash }); navigate(`#/wordlist/${res.wordlist.id}`); }
    catch (err) { toast(err.message, "err", 5000); }
  };
  const list = el("div", { class: "folder-pick" },
    ...docs.map(d => el("button", { class: "folder-pick-row", onclick: () => pick(d.file_hash) },
      icon("doc", "sm"), el("span", { style: "flex:1;text-align:left" }, d.file_name))));
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" }, el("h3", {}, t("wordlist_new_doc")),
      el("p", { style: "margin:0 0 10px;color:var(--muted);font-size:13px" }, t("wordlist_gen_from_doc")), list),
    el("div", { class: "confirm-foot" }, el("button", { class: "btn", onclick: () => close() }, t("cancel")))),
    { center: true, small: true });
}

// `pickFolderId` zet de homepagina in kiesstand: je komt hier vanuit een map
// via "Bestanden toevoegen". De losse documenten krijgen dan een vinkje en de
// pagina scrolt meteen naar het kopje "Nog niet in een vak", zodat je niet
// zelf hoeft te zoeken waar je moet zijn.
export function renderHome(root, pickFolderId = null) {
  const topbar = el("div", { class: "topbar" },
    brandMark(),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost", onclick: () => openSearch({ onPick: (h) => navigate(`#/doc/${h.file_hash}/study/${h.page_index}`) }) },
      icon("search", "sm"), t("search")),
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

  // Twee snelle acties náást het uploaden van een heel college: los iets
  // snappen (foto), of een woordenlijst maken. Los van de zware documentweergave.
  const actionRow = el("div", { class: "home-actions" },
    el("button", { class: "btn lg", onclick: () => navigate("#/quick") }, icon("zap", "sm"), t("quick_action")),
    el("button", { class: "btn lg", onclick: () => openWordlistMenu() }, icon("book", "sm"), t("wordlist_action")),
  );

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
  loadRecent(recentSection, pickFolderId);

  const home = el("div", { class: "home" },
    heroBackdrop(),
    el("div", { class: "home-inner" },
      heroBlock(),
      uploadArea,
      actionRow,
      recentSection,
    ),
  );

  root.append(topbar, home);
}

// Hero-titel met een vaste aanhef en een roterende, in accentkleur getypte zin
// (de verschillende dingen die de app doet). Vertaalt mee via tList().
function heroBlock() {
  const rotate = tList("hero_rotate");
  const typeEl = el("span", { class: "hero-type" });
  const caret = el("span", { class: "hero-caret", "aria-hidden": "true" });
  // aria-label geeft screenreaders één rustige zin i.p.v. de tikkende tekst.
  const firstPlain = rotate.length ? plainText(parseSegments(rotate[0])) : "";
  const h1 = el("h1", { "aria-label": `${t("hero_prefix")} ${firstPlain}`.trim() },
    t("hero_prefix"),
    el("span", { class: "hero-type-line" }, typeEl, caret),
  );
  startTypewriter(typeEl, rotate);
  return el("div", { class: "hero" }, h1, el("p", {}, t("hero_sub")));
}

// Splitst een zin op *sterretjes*: stukken tussen sterretjes zijn groen (g:true),
// de rest krijgt de normale tekstkleur. Zo staan de werkwoorden in het groen.
function parseSegments(phrase) {
  const segs = [];
  const re = /\*([^*]+)\*|([^*]+)/g;
  let m;
  while ((m = re.exec(phrase))) segs.push(m[1] != null ? { t: m[1], g: true } : { t: m[2], g: false });
  return segs;
}
function plainText(segs) { return segs.map((s) => s.t).join(""); }

// Toont de eerste n tekens van een reeks segmenten; groene stukken in een eigen
// span, zodat de kleur per woord blijft kloppen terwijl er wordt getypt.
function renderSegments(node, segs, n) {
  const kids = [];
  let left = n;
  for (const s of segs) {
    if (left <= 0) break;
    const slice = s.t.slice(0, left);
    left -= slice.length;
    kids.push(s.g ? el("span", { class: "g" }, slice) : document.createTextNode(slice));
  }
  node.replaceChildren(...kids);
}

// Typt de zinnen om en om (typen → pauze → wissen → volgende). Stopt vanzelf
// zodra de home-view uit de DOM is (router doet replaceChildren). Respecteert
// prefers-reduced-motion: dan gewoon de eerste zin, zonder animatie.
function startTypewriter(node, phrases) {
  const list = (phrases || []).filter(Boolean).map((p) => {
    const segs = parseSegments(p);
    return { segs, len: plainText(segs).length };
  });
  if (!list.length) return;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || list.length === 1) {
    renderSegments(node, list[0].segs, list[0].len);
    node.parentElement?.classList.add("static");
    return;
  }
  let pi = 0, ci = 0, deleting = false;
  const tick = () => {
    if (!node.isConnected) return;  // view is weg → animatie stopt vanzelf
    const cur = list[pi];
    if (!deleting) {
      renderSegments(node, cur.segs, ++ci);
      if (ci >= cur.len) { deleting = true; return void setTimeout(tick, 1500); }
    } else {
      renderSegments(node, cur.segs, --ci);
      if (ci <= 0) { deleting = false; pi = (pi + 1) % list.length; return void setTimeout(tick, 350); }
    }
    setTimeout(tick, deleting ? 32 : 60 + Math.random() * 45);
  };
  node.replaceChildren();
  setTimeout(tick, 550);
}

// Rustige lijntjes-achtergrond achter de hero (knooppunten + schuine lijnen).
// Kleuren komen uit de theme-variabelen, dus goed in light én dark.
function heroBackdrop() {
  const svg = `<svg viewBox="0 0 1200 340" xmlns="http://www.w3.org/2000/svg" focusable="false">
    <path class="ln" d="M40 300 H1160"/>
    <path class="ln" d="M140 300 L40 64"/>
    <path class="ln" d="M430 300 L250 64"/>
    <path class="ln" d="M770 300 L950 64"/>
    <path class="ln" d="M1060 300 L1160 64"/>
    <circle class="dot" cx="40" cy="64" r="3"/>
    <circle class="dot" cx="250" cy="64" r="3"/>
    <circle class="dot" cx="950" cy="64" r="3"/>
    <circle class="dot" cx="1160" cy="64" r="3"/>
    <circle class="nd" cx="140" cy="300" r="6"/>
    <circle class="nd" cx="430" cy="300" r="6"/>
    <circle class="nd ctr" cx="600" cy="300" r="9"/>
    <circle class="nd" cx="770" cy="300" r="6"/>
    <circle class="nd" cx="1060" cy="300" r="6"/>
  </svg>`;
  return el("div", { class: "hero-bg", "aria-hidden": "true", html: svg });
}

async function loadRecent(container, pickFolderId = null) {
  let docs, folders, wordlists;
  try {
    const [docsData, foldersData, wlData] = await Promise.all([
      api.getDocuments(), api.folders(), api.wordlists().catch(() => ({ wordlists: [] })),
    ]);
    docs = docsData.documents || [];
    folders = foldersData.folders || [];
    wordlists = wlData.wordlists || [];
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

  const refresh = () => loadRecent(container, pickFolderId);
  const kids = [];

  // In kiesstand hoort de pagina maar over één ding te gaan: aanvinken wat er
  // in dit vak moet. De rest van de homepagina blijft staan als context, maar
  // de banner en de actiebalk horen bij de selectie.
  const pickTarget = pickFolderId ? folders.find(f => f.id === pickFolderId) : null;
  const picked = new Set();
  let unfiledAnchor = null, pickBar = null, pickCountLabel = null;
  const paintPickBar = () => {
    if (!pickCountLabel) return;
    pickCountLabel.textContent = t("pick_selected", { n: picked.size });
    pickBar.querySelector(".pick-confirm").disabled = picked.size === 0;
  };

  /* ---------- ga verder waar je was ---------- */
  // Bewust over álle documenten heen, ook die in een vak zitten: hiervoor stond
  // hier alleen materiaal zónder map, waardoor het college dat je zojuist las
  // ontbrak zodra je het had opgeborgen — precies het tegenovergestelde van wat
  // de kop belooft. Kort lijstje: dit is de snelle weg terug, geen archief.
  const opened = docs
    .filter(d => d.last_opened_at && d.kind !== "quick" && d.kind !== "exercise")
    .sort((a, b) => (b.last_opened_at || 0) - (a.last_opened_at || 0))
    .slice(0, 4);
  if (opened.length) {
    const grid = el("div", { class: "doc-grid" });
    for (const d of opened) grid.append(docCard(d, folders, refresh));
    kids.push(el("div", { class: "section-title" }, t("resume_title"), el("span", { class: "line" })), grid);
  }

  /* ---------- mappen (vakken) ---------- */
  if (folders.length || docs.length) {
    const newFolderBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" },
      icon("folder-plus", "sm"), t("new_folder"));
    newFolderBtn.addEventListener("click", () => promptFolderName(refresh));

    kids.push(el("div", { class: "section-title" }, t("folders_title"), el("span", { class: "line" }), newFolderBtn));

    // Alleen de bovenste laag: submappen staan in de map waar ze bij horen.
    const topFolders = folders.filter(f => !f.parent_id);
    if (topFolders.length) {
      kids.push(el("div", { class: "folder-grid" },
        ...topFolders.map(f => folderCard(f)),
      ));
    } else {
      kids.push(el("p", { style: "margin:0 0 8px;font-size:12.5px;color:var(--muted)" }, t("folders_hint")));
    }
  }

  /* ---------- woordenlijsten ---------- */
  if (wordlists.length) {
    kids.push(el("div", { class: "section-title" }, t("wordlists_title"), el("span", { class: "line" })));
    kids.push(el("div", { class: "folder-grid" },
      ...wordlists.map(w => el("button", { class: "folder-card", onclick: () => navigate(`#/wordlist/${w.id}`) },
        el("span", { class: "f-icon" }, icon("book")),
        el("div", { style: "flex:1;min-width:0;text-align:left" },
          el("div", { class: "f-name" }, w.name),
          el("div", { class: "f-meta" }, `${w.count} ${t("wordlist_terms")}`)),
        icon("right", "sm"),
      ))));
  }

  /* ---------- documenten die (nog) niet in een vak zitten ---------- */
  const unfiled = docs.filter(d => !d.folder_id && d.kind !== "quick" && d.kind !== "exercise");
  if (unfiled.length) {
    const grid = el("div", { class: "doc-grid" });
    for (const d of unfiled) {
      grid.append(docCard(d, folders, refresh, pickTarget ? {
        isPicked: () => picked.has(d.file_hash),
        toggle: (on) => { if (on) picked.add(d.file_hash); else picked.delete(d.file_hash); paintPickBar(); },
      } : null));
    }
    unfiledAnchor = el("div", { class: "section-title" }, t("unfiled_title"), el("span", { class: "line" }));
    kids.push(unfiledAnchor, grid);
    // Buiten de kiesstand houden we exact twee visuele rijen in beeld. Het
    // aantal kolommen verandert responsief, dus op basis van de echte offsetTop
    // bepalen we welke kaarten in rij 1 en 2 vallen.
    if (!pickTarget && unfiled.length > 2) kids.push(makeTwoRowToggle(grid));
  } else if (pickTarget) {
    unfiledAnchor = el("div", { class: "section-title" }, t("unfiled_title"), el("span", { class: "line" }));
    kids.push(unfiledAnchor,
      el("div", { class: "empty-state" }, el("p", {}, t("folder_add_docs_none"))));
  }

  if (pickTarget) {
    kids.unshift(pickBanner(pickTarget));
    pickBar = buildPickBar(pickTarget, picked);
    pickCountLabel = pickBar.querySelector(".pick-count");
    kids.push(el("div", { style: "height:84px" }));   // ruimte onder de vaste balk
    paintPickBar();
  }

  if (kids.length) container.replaceChildren(...kids);
  if (pickBar) container.append(pickBar);

  // Naar het juiste kopje scrollen gebeurt ná het invoegen, anders staat het
  // element nog niet op zijn definitieve plek.
  if (pickTarget && unfiledAnchor) {
    requestAnimationFrame(() => unfiledAnchor.scrollIntoView({ behavior: "smooth", block: "start" }));
  }
}

function makeTwoRowToggle(grid) {
  let expanded = false;
  let hiddenCount = 0;
  const button = el("button", { class: "btn ghost doc-grid-more", hidden: true });

  const layout = () => {
    const cards = [...grid.children];
    for (const card of cards) card.hidden = false;
    if (expanded) {
      hiddenCount = 0;
      button.hidden = false;
      button.replaceChildren(icon("up", "sm"), t("show_less"));
      return;
    }
    const rowTops = [];
    for (const card of cards) {
      if (!rowTops.some(top => Math.abs(top - card.offsetTop) < 2)) rowTops.push(card.offsetTop);
    }
    const cutoff = rowTops[1];
    for (const card of cards) card.hidden = cutoff != null && card.offsetTop > cutoff + 1;
    hiddenCount = cards.filter(card => card.hidden).length;
    button.hidden = hiddenCount === 0;
    if (hiddenCount) button.replaceChildren(icon("right", "sm"), t("show_more", { n: hiddenCount }));
  };

  button.addEventListener("click", () => { expanded = !expanded; layout(); });
  const observer = new ResizeObserver(() => requestAnimationFrame(layout));
  observer.observe(grid);
  requestAnimationFrame(layout);
  return button;
}

// Kaartje van één map op de homepagina. Toont hoeveel er in totaal in zit
// (submappen meegeteld), want dat is wat je eraan afleest als "hoe groot is dit vak".
function folderCard(f) {
  const total = f.total_document_count ?? f.document_count;
  const metaParts = [folderCountLabel(total)];
  if (f.subfolder_count) metaParts.push(subfolderCountLabel(f.subfolder_count));
  return el("button", { class: "folder-card", onclick: () => navigate(`#/folder/${f.id}`) },
    el("span", { class: "f-icon" }, icon("folder")),
    el("div", { style: "flex:1;min-width:0;text-align:left" },
      el("div", { class: "f-name" }, f.name),
      el("div", { class: "f-meta" }, metaParts.join(" · "))),
    icon("right", "sm"),
  );
}

function pickBanner(folder) {
  return el("div", { class: "pick-banner" },
    el("span", { class: "f-icon" }, icon("folder")),
    el("div", { style: "flex:1;min-width:0" },
      el("strong", {}, t("pick_title", { name: folder.name })),
      el("div", { style: "font-size:12.5px;color:var(--muted)" }, t("pick_sub")),
    ),
  );
}

function buildPickBar(folder, picked) {
  const count = el("span", { class: "pick-count" });
  const confirm = el("button", { class: "btn primary pick-confirm", disabled: true },
    icon("check", "sm"), t("pick_add"));
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    const hashes = [...picked];
    try {
      // Eén call per document; de backend kent geen bulk-endpoint en bij deze
      // aantallen is parallel sturen ruim snel genoeg.
      await Promise.all(hashes.map(h => api.setDocumentFolder(h, folder.id)));
      toast(hashes.length === 1 ? t("folder_add_docs_done_one")
                                : t("folder_add_docs_done", { n: hashes.length }), "ok");
      navigate(`#/folder/${folder.id}`);
    } catch (err) {
      confirm.disabled = false;
      toast(err.message, "err");
    }
  });
  return el("div", { class: "pick-bar" },
    count,
    el("span", { class: "spacer" }),
    el("button", { class: "btn", onclick: () => navigate(`#/folder/${folder.id}`) }, t("cancel")),
    confirm,
  );
}

// `pick` (optioneel) zet de kaart in aanvinkstand: klikken selecteert in plaats
// van het document te openen, en de hover-knoppen zijn dan niet van toepassing.
function docCard(d, folders, refresh, pick = null) {
  const lastPage = Math.max(0, Math.min(d.last_page_index || 0, Math.max(0, d.total_pages - 1)));
  const progress = d.total_pages > 1 ? (lastPage + 1) / d.total_pages : 1;
  const thumb = d.thumbnail_url
    ? el("img", { loading: "lazy", alt: "" })
    : el("div", { class: "ph" }, icon("image", "lg"));
  if (d.thumbnail_url) api.setImage(thumb, api.base + d.thumbnail_url).catch(() => {});

  const restart = lastPage > 0
    ? el("button", { class: "restart-chip", title: t("start_over"), onclick: (e) => {
        e.stopPropagation();
        navigate(`#/doc/${d.file_hash}/study/0`);
      } }, icon("refresh", "sm"), t("start_over"))
    : null;
  const card = el("button", { class: "doc-card", onclick: () => {
    if (pick) { setPicked(!card.classList.contains("picked")); return; }
    navigate(`#/doc/${d.file_hash}/study/${lastPage}`);
  } },
    el("div", { class: "thumb" }, thumb, restart),
    el("div", { class: "body" },
      el("div", { class: "title" }, d.file_name),
      el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${Math.round(progress * 100)}%` })),
      el("div", { class: "meta" },
        icon("clock", "sm"),
        timeAgo(d.last_opened_at || d.uploaded_at),
        el("span", { style: "margin-left:auto" }, t("slide_frac", { a: lastPage + 1, b: d.total_pages })),
      ),
    ),
  );

  // Bewust geen <input type="checkbox">: de kaart is zelf al een knop, en een
  // invoerveld in een knop is ongeldige HTML die in de praktijk met de klik van
  // de knop vecht. Het vinkje is puur beeld; aria-pressed draagt de staat.
  function setPicked(on) {
    card.classList.toggle("picked", on);
    card.setAttribute("aria-pressed", String(on));
    pick.toggle(on);
  }
  if (pick) {
    card.append(el("span", { class: "card-check-wrap", "aria-hidden": "true" }, icon("check", "sm")));
    card.setAttribute("aria-pressed", "false");
    if (restart) restart.remove();   // in kiesstand opent er niets
    return card;
  }

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
      toast(t("deleted"), "ok");
      refresh(); // hertekent alles: hetzelfde document kan in twee secties staan
    } catch (err) { toast(err.message, "err"); }
  } }, icon("trash", "sm"));
  card.append(moveBtn, del);
  return card;
}

// Nieuwe map aanmaken; onDone krijgt (optioneel) de nieuwe map terug.
// `parentId` maakt de nieuwe map een submap van die map (null = bovenin).
export function promptFolderName(onDone, parentId = null) {
  const input = el("input", { class: "field", placeholder: t("folder_name_ph"), maxlength: "80" });
  let close;
  const createBtn = el("button", { class: "btn primary", onclick: async () => {
    const name = input.value.trim();
    if (!name) return;
    try {
      const res = await api.folderCreate(name, parentId);
      close();
      toast(t("folder_created", { name }), "ok");
      onDone?.(res.folder);
    } catch (err) { toast(err.message, "err"); }
  } }, t("create"));
  close = openModal(el("div", {},
    el("div", { class: "confirm-body" }, el("h3", {}, t(parentId ? "new_subfolder" : "new_folder")), input),
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
    ...folderTree(folders).map(f => el("button", {
      class: "folder-pick-row",
      style: f.depth ? `padding-left:${12 + f.depth * 16}px` : null,
      onclick: () => assign(f.id, f.name),
    },
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

// Platte mappenlijst in boomvolgorde: elke map direct gevolgd door wat eronder
// hangt. `depth` is er voor de inspringing in keuzelijsten. Mappen waarvan de
// bovenliggende map ontbreekt komen bovenin terecht, zodat ze nooit zoekraken.
export function folderTree(folders) {
  const ids = new Set(folders.map(f => f.id));
  const children = new Map();
  for (const f of folders) {
    const parent = f.parent_id && ids.has(f.parent_id) ? f.parent_id : null;
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(f);
  }
  const out = [];
  const walk = (parent, depth) => {
    for (const f of children.get(parent) || []) {
      out.push({ ...f, depth });
      walk(f.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

// Tellingen op mapkaartjes. Apart enkelvoud, want "1 documenten" leest als een bug.
export function folderCountLabel(n) {
  return n === 1 ? t("folder_docs_one") : t("folder_docs", { n });
}
export function subfolderCountLabel(n) {
  return n === 1 ? t("folder_subfolder_one") : t("folder_subfolders", { n });
}
