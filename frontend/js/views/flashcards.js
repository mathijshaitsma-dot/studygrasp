// Flashcards met spaced repetition (backend doet het SM-2-schema).
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import { study } from "../stats.js";
import { navigate } from "../app.js";

export function mountFlashcards(main, ctx, badge) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  loadOverview(page, ctx, badge);
}

async function loadOverview(page, ctx, badge) {
  const { hash } = ctx;
  page.replaceChildren(el("div", { style: "display:grid;place-items:center;padding:70px" }, el("div", { class: "spinner" })));

  let data = null;
  try { data = await api.flashcardsGet(hash); } catch { /* nog geen kaarten */ }
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
      el("button", { class: "btn ghost lg", onclick: () => showGenerate(page, ctx, badge, true) }, icon("refresh", "sm"), t("regenerate")),
    ),
  ));
}

// TSV-download die Anki direct kan importeren (front <tab> back).
function exportAnki(fileName, cards) {
  const clean = (s) => String(s || "").replace(/\t/g, " ").replace(/\n/g, "<br>");
  const tsv = cards.map(c => `${clean(c.front)}\t${clean(c.back)}`).join("\n");
  const blob = new Blob([tsv], { type: "text/tab-separated-values;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${fileName.replace(/\.[^.]+$/, "")} - flashcards.txt`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast(t("anki_done"), "ok", 4500);
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

/* ---------- leersessie ---------- */
function runSession(page, ctx, queue, badge, practice = false) {
  const { hash } = ctx;
  let i = 0;
  let flipped = false;
  let reviewed = 0;

  const onKey = (e) => {
    if (!page.isConnected) { document.removeEventListener("keydown", onKey); return; }
    if (/^(input|textarea)$/i.test(document.activeElement?.tagName || "")) return;
    if (e.key === " ") { e.preventDefault(); flip(); }
    else if (flipped && ["1", "2", "3", "4"].includes(e.key)) {
      rate(["again", "hard", "good", "easy"][+e.key - 1]);
    }
  };
  document.addEventListener("keydown", onKey);

  let cardEl, ratesEl;

  function flip() {
    if (!cardEl) return;
    flipped = !flipped;
    cardEl.classList.toggle("flipped", flipped);
    ratesEl.style.visibility = flipped ? "visible" : "hidden";
  }

  async function rate(rating) {
    const card = queue[i];
    flipped = false;
    // beoordeling telt mee voor "zwakke dia's" in de studeer-weergave
    study.recordScore(hash, card.page_index, { again: 0, hard: 50, good: 85, easy: 100 }[rating]);
    if (!practice) {
      try {
        await api.flashcardsReview({ file_hash: hash, card_id: card.id, rating });
        if (rating === "again") queue.push(card); // "opnieuw" komt later in deze sessie terug
        reviewed++;
      } catch (err) { toast(err.message, "err"); }
    } else if (rating === "again") {
      queue.push(card);
    }
    i++;
    show();
  }

  function show() {
    if (i >= queue.length) {
      document.removeEventListener("keydown", onKey);
      page.replaceChildren(el("div", { class: "content-inner narrow" },
        el("div", { class: "fc-done" },
          el("div", { class: "big" }, "🎉"),
          el("h2", { style: "margin:0 0 6px" }, t("session_done")),
          el("p", { style: "color:var(--muted);margin:0 0 22px" }, practice ? t("practiced_n", { n: queue.length }) : t("reviewed_n", { n: reviewed })),
          el("button", { class: "btn primary lg", onclick: () => loadOverview(page, ctx, badge) }, t("back_overview")),
        )));
      return;
    }
    const card = queue[i];
    flipped = false;

    cardEl = el("div", { class: "fc-card", onclick: flip },
      el("div", { class: "fc-face" },
        el("span", { class: "side-lbl" }, t("side_q")),
        card.page_index != null ? el("button", { class: "chip page-ref", onclick: (e) => { e.stopPropagation(); navigate(`#/doc/${hash}/study/${card.page_index}`); } }, t("slide_chip", { n: card.page_index + 1 })) : null,
        el("div", { class: "md", html: renderMarkdown(card.front) }),
      ),
      el("div", { class: "fc-face back" },
        el("span", { class: "side-lbl" }, t("side_a")),
        el("div", { class: "md", html: renderMarkdown(card.back) }),
      ),
    );

    ratesEl = el("div", { class: "fc-rates", style: "visibility:hidden" },
      rateBtn("again", t("rate_again"), t("rate_again_sub"), () => rate("again")),
      rateBtn("hard", t("rate_hard"), t("rate_hard_sub"), () => rate("hard")),
      rateBtn("good", t("rate_good"), t("rate_good_sub"), () => rate("good")),
      rateBtn("easy", t("rate_easy"), t("rate_easy_sub"), () => rate("easy")),
    );

    page.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "quiz-top" },
        el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${(i / queue.length) * 100}%` })),
        el("span", { class: "count" }, `${i + 1} / ${queue.length}`)),
      el("div", { class: "fc-scene" }, cardEl),
      el("p", { style: "text-align:center;color:var(--muted);font-size:12.5px;margin:0 0 14px" },
        t("flip_pre"), el("kbd", {}, t("space_key")), t("flip_mid"), el("kbd", {}, "1"), "–", el("kbd", {}, "4")),
      ratesEl,
    ));
  }

  function rateBtn(cls, label, sub, onclick) {
    return el("button", { class: `rate-btn ${cls}`, onclick }, label, el("small", {}, sub));
  }

  show();
}
