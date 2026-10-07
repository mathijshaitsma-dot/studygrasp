// Gedeelde flip/beoordeel-sessie voor spaced repetition — gebruikt door zowel
// flashcards (vraag/antwoord) als woordenlijsten (term/definitie). De mechaniek
// (omdraaien, 1-4 beoordelen, voortgang, "opnieuw" terug in de wachtrij) is
// identiek; alleen de labels en het opslaan verschillen per aanroeper.
import { el, icon, toast } from "./util.js";
import { t } from "./i18n.js";
import { renderMarkdown, renderCharts } from "./markdown.js";

// opts: {
//   cards[], labelQ, labelA,
//   front(card)->md, back(card)->md, frontExtra(card)->node|null,
//   onRate(card, rating)->Promise (persisteert; gooit bij fout),
//   onDone(container, reviewedCount, total)  (rendert het slotscherm),
//   practice:boolean
// }
export function runReviewSession(container, opts) {
  const queue = opts.cards;
  let i = 0;
  let flipped = false;
  let reviewed = 0;
  let rating = false;
  let cardEl, ratesEl;

  const onKey = (e) => {
    if (!container.isConnected) { document.removeEventListener("keydown", onKey); return; }
    if (/^(input|textarea|select)$/i.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable) return;
    if (e.repeat || rating) return;
    if (e.key === " ") { e.preventDefault(); flip(); }
    else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      if (!flipped) flip();
      else rate(e.key === "ArrowLeft" ? "again" : "good");
    }
    else if (flipped && ["1", "2", "3", "4"].includes(e.key)) {
      rate(["again", "hard", "good", "easy"][+e.key - 1]);
    }
  };
  document.addEventListener("keydown", onKey);

  function flip() {
    if (!cardEl) return;
    flipped = !flipped;
    cardEl.classList.toggle("flipped", flipped);
    cardEl.setAttribute("aria-expanded", String(flipped));
    const [front, back] = cardEl.querySelectorAll(".fc-face");
    front?.setAttribute("aria-hidden", String(flipped));
    back?.setAttribute("aria-hidden", String(!flipped));
    ratesEl.hidden = !flipped;
    if (flipped) cardEl.querySelector(".fc-face.back")?.focus();
  }

  async function rate(ratingValue) {
    if (reviewing()) return;
    setReviewing(true);
    const card = queue[i];
    flipped = false;
    try {
      await opts.onRate(card, ratingValue);
      if (ratingValue === "again") queue.push(card); // "opnieuw" komt later terug in deze sessie
      reviewed++;
      i++;
      setReviewing(false);
      show();
    } catch (err) {
      toast(err.message, "err");
      setReviewing(false);
      flipped = true;
      cardEl?.classList.add("flipped");
      cardEl?.setAttribute("aria-expanded", "true");
      const [front, back] = cardEl?.querySelectorAll(".fc-face") || [];
      front?.setAttribute("aria-hidden", "true");
      back?.setAttribute("aria-hidden", "false");
      if (ratesEl) ratesEl.hidden = false;
    }
  }

  function reviewing() { return rating; }
  function setReviewing(on) {
    rating = on;
    ratesEl?.querySelectorAll("button").forEach(button => { button.disabled = on; });
  }

  function rateBtn(cls, label, sub, onclick) {
    return el("button", { class: `rate-btn ${cls}`, onclick }, label, el("small", {}, sub));
  }

  function show() {
    if (i >= queue.length) {
      document.removeEventListener("keydown", onKey);
      opts.onDone(container, reviewed, queue.length);
      return;
    }
    const card = queue[i];
    flipped = false;

    const frontBox = el("div", { class: "md", html: renderMarkdown(opts.front(card)) });
    const backBox = el("div", { class: "md", html: renderMarkdown(opts.back(card)) });
    renderCharts(frontBox); renderCharts(backBox);

    cardEl = el("div", {
      class: "fc-card", role: "button", tabindex: "0", onclick: flip,
      "aria-expanded": "false", "aria-label": `${opts.labelQ}: ${opts.front(card)}`,
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); flip(); }
      },
    },
      el("div", { class: "fc-face", "aria-hidden": "false" },
        el("span", { class: "side-lbl" }, opts.labelQ),
        opts.frontExtra ? opts.frontExtra(card) : null,
        frontBox),
      el("div", { class: "fc-face back", "aria-hidden": "true", tabindex: "-1", "aria-live": "polite" },
        el("span", { class: "side-lbl" }, opts.labelA),
        backBox),
    );

    ratesEl = el("div", { class: "fc-rates", hidden: true, "aria-label": t("review_rating_label") },
      rateBtn("again", t("rate_again"), t("rate_again_sub"), () => rate("again")),
      rateBtn("hard", t("rate_hard"), t("rate_hard_sub"), () => rate("hard")),
      rateBtn("good", t("rate_good"), t("rate_good_sub"), () => rate("good")),
      rateBtn("easy", t("rate_easy"), t("rate_easy_sub"), () => rate("easy")),
    );

    container.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "quiz-top" },
        el("div", { class: "progress-track" }, el("div", { class: "progress-fill", style: `width:${(i / queue.length) * 100}%` })),
        el("span", { class: "count" }, `${i + 1} / ${queue.length}`)),
      el("div", { class: "fc-scene" }, cardEl),
      el("p", { style: "text-align:center;color:var(--muted);font-size:12.5px;margin:0 0 14px" },
        t("flip_pre"), el("kbd", {}, t("space_key")), t("flip_mid"), el("kbd", {}, "1"), "–", el("kbd", {}, "4"),
        el("span", { class: "fc-arrow-hint" }, t("arrow_review_hint"))),
      ratesEl,
    ));
  }

  show();
}

// Herbruikbaar slotscherm.
export function doneScreen(container, message, onBack) {
  container.replaceChildren(el("div", { class: "content-inner narrow" },
    el("div", { class: "fc-done" },
      el("div", { class: "big" }, "🎉"),
      el("h2", { style: "margin:0 0 6px" }, t("session_done")),
      el("p", { style: "color:var(--muted);margin:0 0 22px" }, message),
      el("button", { class: "btn primary lg", onclick: onBack }, t("back_overview")),
    )));
}
