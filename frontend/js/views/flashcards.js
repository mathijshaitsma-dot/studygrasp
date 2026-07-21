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
    try {
      await api.flashcardsGenerate({ file_hash: hash, language: prefs.language, max_cards: maxCards, force_refresh: isRegen });
      toast(t("cards_ready"), "ok");
      loadOverview(page, ctx, badge);
    } catch (err) {
      toast(err.message, "err", 5000);
      genBtn.disabled = false;
      genBtn.replaceChildren(icon("sparkle", "sm"), t("gen_cards"));
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
