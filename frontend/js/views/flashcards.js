// Flashcards met spaced repetition (backend doet het SM-2-schema).
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { study } from "../stats.js";
import { navigate } from "../app.js";
import { downloadText } from "../export.js";
import { runReviewSession, doneScreen } from "../review.js";

export function mountFlashcards(main, ctx, badge) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  loadOverview(page, ctx, badge);
}

async function loadOverview(page, ctx, badge) {
  const { hash } = ctx;
  page.replaceChildren(el("div", { style: "display:grid;place-items:center;padding:70px" }, el("div", { class: "spinner" })));

  let data = null;
  try { data = await api.flashcardsGet(hash, prefs.language); } catch { /* nog geen kaarten */ }
  const cards = data?.cards || [];

  if (!cards.length) { showGenerate(page, ctx, badge); return; }

  const due = cards.filter(c => c.is_due);
  const dueCount = data.due_count ?? due.length;
  if (badge) {
    badge.textContent = dueCount;
    badge.style.display = dueCount > 0 ? "" : "none";
  }
  const learned = cards.filter(c => (c.reps || 0) > 0).length;

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cards"), t("tab_cards")),
    el("p", { class: "page-sub" }, t("cards_sub")),
    el("div", { class: "fc-stats" },
      statCard(cards.length, t("stat_total")),
      statCard(dueCount, t("stat_due"), dueCount > 0 ? "var(--accent)" : null),
      statCard(learned, t("stat_learned")),
    ),
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap" },
      el("button", { class: "btn primary lg", disabled: dueCount === 0, onclick: () => runSession(page, ctx, due, badge) },
        icon("play", "sm"), dueCount > 0 ? t("review_n", { n: dueCount }) : t("all_done")),
      el("button", { class: "btn lg", onclick: () => runSession(page, ctx, [...cards], badge, true) }, icon("cards", "sm"), t("practice_all")),
      el("button", { class: "btn ghost lg", onclick: () => exportAnki(ctx.doc.file_name, cards) }, icon("download", "sm"), t("export_anki")),
      el("button", { class: "btn ghost lg", onclick: () => exportMarkdown(ctx.doc.file_name, cards) }, icon("doc", "sm"), t("export_markdown")),
      el("button", { class: "btn ghost lg", onclick: () => showGenerate(page, ctx, badge, true) }, icon("refresh", "sm"), t("regenerate")),
    ),
  ));
}

// TSV-download die Anki direct kan importeren (front <tab> back).
function exportAnki(fileName, cards) {
  const clean = (s) => String(s || "").replace(/\t/g, " ").replace(/\n/g, "<br>");
  const tsv = cards.map(c => `${clean(c.front)}\t${clean(c.back)}`).join("\n");
  downloadText(`${fileName.replace(/\.[^.]+$/, "")} - flashcards.txt`, "text/tab-separated-values", tsv);
  toast(t("anki_done"), "ok", 4500);
}

// Markdown-download — werkt direct met Notion/Obsidian's eigen import.
function exportMarkdown(fileName, cards) {
  const md = cards.map((c, i) => `## ${i + 1}\n**${t("side_q")}:** ${c.front}\n\n**${t("side_a")}:** ${c.back}\n`).join("\n");
  downloadText(`${fileName.replace(/\.[^.]+$/, "")} - flashcards.md`, "text/markdown", md);
  toast(t("md_done"), "ok", 4500);
}

function statCard(num, label, color) {
  return el("div", { class: "stat-card" },
    el("div", { class: "num", style: color ? `color:${color}` : "" }, String(num)),
    el("div", { class: "lbl" }, label));
}

function showGenerate(page, ctx, badge, isRegen = false) {
  const { hash, doc } = ctx;
  let maxCards = 25;
  const countVal = el("span", { class: "chip accent" }, t("max_cards", { n: maxCards }));
  const slider = el("input", { type: "range", min: "5", max: "60", value: String(maxCards), style: "width:100%" });
  slider.addEventListener("input", () => { maxCards = +slider.value; countVal.textContent = t("max_cards", { n: maxCards }); });

  const genBtn = el("button", { class: "btn primary lg", style: "align-self:flex-start" }, icon("sparkle", "sm"), t("gen_cards"));
  genBtn.addEventListener("click", async () => {
    genBtn.disabled = true;
    genBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }), t("gen_cards_busy"));
    let elapsed = 0;
    const timer = setInterval(() => {
      elapsed++;
      genBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }),
        t("generating_elapsed", { label: t("gen_cards_busy"), n: elapsed }));
    }, 1000);
    try {
      await api.flashcardsGenerate({ file_hash: hash, language: prefs.language, max_cards: maxCards, force_refresh: isRegen });
      toast(t("cards_ready"), "ok");
      loadOverview(page, ctx, badge);
    } catch (err) {
      toast(err.message, "err", 5000);
      genBtn.disabled = false;
      genBtn.replaceChildren(icon("sparkle", "sm"), t("gen_cards"));
    } finally {
      clearInterval(timer);
    }
  });

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cards"), t("tab_cards")),
    el("p", { class: "page-sub" }, t("gen_sub", { name: doc.file_name })),
    el("div", { class: "setup-card" },
      el("div", { class: "setup-row" },
        el("div", { style: "display:flex;justify-content:space-between;align-items:center" }, el("label", {}, t("count_cards")), countVal),
        slider),
      isRegen ? el("p", { style: "margin:0;font-size:12.5px;color:var(--amber)" }, t("regen_warning")) : null,
      genBtn,
    ),
  ));
}

/* ---------- leersessie (via de gedeelde review-runner) ---------- */
function runSession(page, ctx, queue, badge, practice = false) {
  const { hash } = ctx;
  runReviewSession(page, {
    cards: queue,
    labelQ: t("side_q"),
    labelA: t("side_a"),
    front: (card) => card.front,
    back: (card) => card.back,
    frontExtra: (card) => card.page_index != null
      ? el("button", { class: "chip page-ref", onclick: (e) => { e.stopPropagation(); navigate(`#/doc/${hash}/study/${card.page_index}`); } }, t("slide_chip", { n: card.page_index + 1 }))
      : null,
    onRate: async (card, rating) => {
      // beoordeling telt mee voor "zwakke dia's" in de studeer-weergave
      study.recordScore(hash, card.page_index, { again: 0, hard: 50, good: 85, easy: 100 }[rating]);
      if (!practice) {
        await api.flashcardsReview({ file_hash: hash, card_id: card.id, rating, language: prefs.language });
      }
    },
    onDone: (container, reviewed, total) => doneScreen(
      container,
      practice ? t("practiced_n", { n: total }) : t("reviewed_n", { n: reviewed }),
      () => loadOverview(page, ctx, badge)),
  });
}

/* ================= flashcards over een heel vak ================= */
// Bewust geen eigen kaartenbak: dit voegt de sets van alle documenten in de map
// (en zijn submappen) samen. Een beurt die je hier geeft telt dus gewoon mee in
// het college waar de kaart bij hoort, en andersom. Daarom draagt elke kaart
// zijn eigen file_hash mee.

export function mountFolderFlashcards(main, folder) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  loadFolderOverview(page, folder);
}

async function loadFolderOverview(page, folder) {
  page.replaceChildren(el("div", { style: "display:grid;place-items:center;padding:70px" }, el("div", { class: "spinner" })));

  let data = null;
  try {
    data = await api.folderFlashcards(folder.id, prefs.language);
  } catch (err) {
    page.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "md-error" }, icon("alert"), el("div", {}, err.message))));
    return;
  }

  const cards = data.cards || [];
  const documents = data.documents || [];
  const missing = documents.filter(d => !d.card_count);

  if (!cards.length) {
    showFolderGenerate(page, folder, documents);
    return;
  }

  const due = cards.filter(c => c.is_due);
  const dueCount = data.due_count ?? due.length;
  const learned = cards.filter(c => (c.reps || 0) > 0).length;

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cards"), t("folder_cards_btn")),
    el("p", { class: "page-sub" }, folderCardsSub(folder, documents.length)),
    el("div", { class: "fc-stats" },
      statCard(cards.length, t("stat_total")),
      statCard(dueCount, t("stat_due"), dueCount > 0 ? "var(--accent)" : null),
      statCard(learned, t("stat_learned")),
    ),
    el("div", { style: "display:flex;gap:10px;flex-wrap:wrap" },
      el("button", { class: "btn primary lg", disabled: dueCount === 0, onclick: () => runFolderSession(page, folder, due) },
        icon("play", "sm"), dueCount > 0 ? t("review_n", { n: dueCount }) : t("all_done")),
      el("button", { class: "btn lg", onclick: () => runFolderSession(page, folder, [...cards], true) }, icon("cards", "sm"), t("practice_all")),
      el("button", { class: "btn ghost lg", onclick: () => exportAnki(folder.name, cards) }, icon("download", "sm"), t("export_anki")),
      el("button", { class: "btn ghost lg", onclick: () => exportMarkdown(folder.name, cards) }, icon("doc", "sm"), t("export_markdown")),
      el("button", { class: "btn ghost lg",
        onclick: () => showFolderGenerate(page, folder, documents,
                                          { mode: "regen", totalDocs: documents.length }) },
        icon("refresh", "sm"), t("regenerate")),
    ),
    // Colleges zonder kaarten vallen anders stilletjes buiten de sessie — dat
    // is precies het soort gat waar je bij een tentamen achter komt.
    missing.length
      ? el("div", { class: "setup-card", style: "margin-top:18px" },
          el("p", { style: "margin:0;font-size:13px;color:var(--text-soft)" },
            missing.length === 1 ? t("folder_cards_missing_one")
                                 : t("folder_cards_missing", { n: missing.length })),
          el("button", { class: "btn", style: "align-self:flex-start",
            onclick: () => showFolderGenerate(page, folder, missing,
                                              { mode: "missing", totalDocs: documents.length }) },
            icon("sparkle", "sm"), t("folder_cards_generate_missing")),
        )
      : null,
  ));
}

// Zelfde instellingen als bij één document. Het aantal geldt hier per college
// en niet voor het vak als geheel: de kaarten worden per document gemaakt en
// bewaard, en één bovengrens voor een heel vak zou betekenen dat een lang
// college minder kaarten krijgt naarmate je er meer colleges bij zet.
function showFolderGenerate(page, folder, documents, { mode = "all", totalDocs } = {}) {
  const isRegen = mode === "regen";
  let maxCards = 25;
  const countVal = el("span", { class: "chip accent" }, t("max_cards", { n: maxCards }));
  const slider = el("input", { type: "range", min: "5", max: "60", value: String(maxCards), style: "width:100%" });
  slider.addEventListener("input", () => { maxCards = +slider.value; countVal.textContent = t("max_cards", { n: maxCards }); });

  const genBtn = el("button", { class: "btn primary lg" }, icon("sparkle", "sm"), t("gen_cards"));
  genBtn.addEventListener("click", () => generateForDocs(page, folder, documents, { maxCards, force: isRegen }));

  // Terugweg: zonder dit zit je vast in het instelscherm zodra er al kaarten zijn.
  const backBtn = documents.length && mode !== "all"
    ? el("button", { class: "btn ghost", onclick: () => loadFolderOverview(page, folder) }, t("cancel"))
    : null;

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cards"), t("folder_cards_btn")),
    el("p", { class: "page-sub" }, folderCardsSub(folder, totalDocs ?? documents.length)),
    documents.length
      ? el("div", { class: "setup-card" },
          el("p", { style: "margin:0;font-size:13px;color:var(--text-soft)" },
            generateIntro(mode, documents.length)),
          el("div", { class: "setup-row" },
            el("div", { style: "display:flex;justify-content:space-between;align-items:center" },
              el("label", {}, t("count_cards")), countVal),
            slider,
            el("p", { style: "margin:6px 0 0;font-size:12px;color:var(--muted)" }, t("folder_cards_count_hint")),
          ),
          isRegen ? el("p", { style: "margin:0;font-size:12.5px;color:var(--amber)" }, t("regen_warning")) : null,
          el("div", { style: "display:flex;gap:8px;flex-wrap:wrap" }, genBtn, backBtn),
        )
      : el("div", { class: "empty-state" }, el("p", {}, t("folder_empty"))),
  ));
}

// Enkelvoud apart, want "1 documenten" leest als een bug.
function folderCardsSub(folder, n) {
  return n === 1
    ? t("folder_cards_sub_one", { name: folder.name })
    : t("folder_cards_sub", { name: folder.name, n });
}

function generateIntro(mode, n) {
  const one = n === 1;
  if (mode === "regen") return one ? t("folder_cards_regen_sub_one") : t("folder_cards_regen_sub", { n });
  if (mode === "missing") return one ? t("folder_cards_missing_sub_one") : t("folder_cards_missing_sub", { n });
  return one ? t("folder_cards_gen_sub_one") : t("folder_cards_gen_sub", { n });
}

// Eén document tegelijk: de backend rekent per document af en dedupliceert per
// document, dus parallel sturen zou alleen de quota-melding onduidelijk maken.
// Bij een vak van tien colleges duurt dat even, dus er is een stopknop — wat al
// klaar is blijft staan, want elk document wordt los opgeslagen.
async function generateForDocs(page, folder, documents, { maxCards = 25, force = false } = {}) {
  let stopped = false;
  const status = el("p", { style: "margin:0;font-size:13px;color:var(--text-soft)" });
  const fill = el("div", { class: "progress-fill", style: "width:0%" });
  const stopBtn = el("button", { class: "btn ghost", style: "align-self:flex-start",
    onclick: () => { stopped = true; stopBtn.disabled = true; } }, icon("x", "sm"), t("stop"));
  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cards"), t("folder_cards_btn")),
    el("div", { class: "setup-card" },
      status,
      el("div", { class: "progress-track" }, fill),
      stopBtn,
    ),
  ));

  let done = 0, failed = 0;
  for (const d of documents) {
    if (stopped) break;
    status.textContent = t("folder_cards_gen_busy", { name: d.file_name, i: done + 1, n: documents.length });
    try {
      await api.flashcardsGenerate({
        file_hash: d.file_hash, language: prefs.language,
        max_cards: maxCards, force_refresh: force,
      });
    } catch (err) {
      failed++;
      toast(`${d.file_name}: ${err.message}`, "err", 5000);
    }
    done++;
    fill.style.width = `${Math.round((done / documents.length) * 100)}%`;
  }
  if (done > failed) toast(t("cards_ready"), "ok");
  loadFolderOverview(page, folder);
}

function runFolderSession(page, folder, queue, practice = false) {
  runReviewSession(page, {
    cards: queue,
    labelQ: t("side_q"),
    labelA: t("side_a"),
    front: (card) => card.front,
    back: (card) => card.back,
    // In een vak-sessie komen de kaarten uit verschillende colleges, dus staat
    // erbij uit welk college deze kaart komt — anders is de dia-verwijzing
    // dubbelzinnig.
    frontExtra: (card) => card.page_index != null
      ? el("button", { class: "chip page-ref", onclick: (e) => {
          e.stopPropagation();
          navigate(`#/doc/${card.file_hash}/study/${card.page_index}`);
        } }, `${card.file_name} · ${t("slide_chip", { n: card.page_index + 1 })}`)
      : el("span", { class: "chip" }, card.file_name),
    onRate: async (card, rating) => {
      study.recordScore(card.file_hash, card.page_index, { again: 0, hard: 50, good: 85, easy: 100 }[rating]);
      if (!practice) {
        await api.flashcardsReview({ file_hash: card.file_hash, card_id: card.id, rating, language: prefs.language });
      }
    },
    onDone: (container, reviewed, total) => doneScreen(
      container,
      practice ? t("practiced_n", { n: total }) : t("reviewed_n", { n: reviewed }),
      () => loadFolderOverview(page, folder)),
  });
}
