// Woordenlijst: bewerkbare term/definitie-rijen + oefenen met spaced repetition
// (dezelfde review-runner als flashcards). Een woordenlijst staat los van
// documenten en is bereikbaar via de home-sectie "Mijn woordenlijsten".
import { api } from "../api.js";
import { el, icon, brandMark, toast, confirmDialog, debounce } from "../util.js";
import { t } from "../i18n.js";
import { guestLoginButton, navigate } from "../app.js";
import { runReviewSession, doneScreen } from "../review.js";
import { downloadText } from "../export.js";
import { prefs, savePrefs } from "../state.js";
import { isTypedAnswerCorrect, makeTypingQueue, scheduleIncorrectCard, wordlistSides } from "../wordlist-typing.js";

export async function renderWordlist(root, id) {
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);

  let wl;
  try { wl = (await api.wordlistGet(id)).wordlist; }
  catch (err) {
    loading.remove();
    root.append(el("div", { style: "flex:1;display:grid;place-items:center;padding:24px" },
      el("div", { class: "empty-state" }, el("p", {}, err.message),
        el("button", { class: "btn primary", style: "margin-top:10px", onclick: () => navigate("#/") }, icon("home", "sm"), t("to_home")))));
    return;
  }
  loading.remove();

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    brandMark(false),
    el("div", { class: "doc-name", title: wl.name }, wl.name),
    el("div", { class: "spacer" }),
    guestLoginButton({ compact: true }),
    el("button", { class: "btn ghost icon-btn", title: t("export_anki"), onclick: () => exportList(wl) }, icon("download")),
    el("button", { class: "btn ghost icon-btn danger", title: t("delete"), onclick: () => remove(wl) }, icon("trash")),
  );
  const main = el("div", { class: "workspace", style: "overflow:auto" });
  root.append(topbar, main);

  renderEditor();

  function renderEditor() {
    const dueCount = wl.cards.filter(c => c.is_due).length;
    const rows = el("div", { class: "wl-rows" });
    const pager = el("div", { style: "display:flex;gap:10px;align-items:center;justify-content:center;margin:14px 0" });
    const pageSize = 25;
    let currentPage = 0;
    let draftCards = wl.cards.map(c => ({ ...c }));
    let practiceDirection = prefs.wordlistDirection === "definition_first" ? "definition_first" : "term_first";

    // debounced opslaan van de hele lijst zodra je typt/toevoegt/verwijdert.
    // Bewust vóór de rijen gedefinieerd: rowEl gebruikt 'save' meteen.
    const save = debounce(async () => {
      const cards = draftCards.map(c => ({ term: (c.term || "").trim(), definition: (c.definition || "").trim() }))
        .filter(c => c.term || c.definition);
      try { wl = (await api.wordlistUpdate(wl.id, { cards })).wordlist; }
      catch (err) { toast(err.message, "err"); }
    }, 700);

    const autoGrow = (field) => {
      field.style.height = "auto";
      field.style.height = `${field.scrollHeight}px`;
    };

    const rowEl = (c, index) => {
      const term = el("textarea", { class: "field wl-term", rows: "1", placeholder: t("wordlist_term_ph"),
        "aria-label": t("side_term") }, c.term || "");
      const def = el("textarea", { class: "field wl-def", rows: "1", placeholder: t("wordlist_def_ph"),
        "aria-label": t("side_def") }, c.definition || "");
      term.addEventListener("input", () => { autoGrow(term); draftCards[index].term = term.value; save(); });
      def.addEventListener("input", () => { autoGrow(def); draftCards[index].definition = def.value; save(); });
      const del = el("button", { class: "btn ghost icon-btn", title: t("delete"), onclick: () => {
        draftCards.splice(index, 1);
        currentPage = Math.min(currentPage, Math.max(0, Math.ceil(draftCards.length / pageSize) - 1));
        renderRows(); save();
      } }, icon("x", "sm"));
      const row = el("div", { class: "wl-row" }, term, def, del);
      requestAnimationFrame(() => { autoGrow(term); autoGrow(def); });
      return row;
    };

    const renderRows = () => {
      rows.replaceChildren();
      const start = currentPage * pageSize;
      draftCards.slice(start, start + pageSize).forEach((c, offset) => rows.append(rowEl(c, start + offset)));
      const pages = Math.max(1, Math.ceil(draftCards.length / pageSize));
      const prev = el("button", { class: "btn ghost", disabled: currentPage === 0,
        title: t("prev"), "aria-label": t("prev"),
        onclick: () => { currentPage--; renderRows(); } }, icon("left", "sm"));
      const next = el("button", { class: "btn ghost", disabled: currentPage >= pages - 1,
        title: t("next"), "aria-label": t("next"),
        onclick: () => { currentPage++; renderRows(); } }, icon("right", "sm"));
      pager.replaceChildren(prev, el("span", { class: "count" }, `${currentPage + 1} / ${pages}`), next);
      pager.hidden = pages <= 1;
    };
    renderRows();

    const addBtn = el("button", { class: "btn ghost", onclick: () => {
      draftCards.push({ term: "", definition: "", id: null });
      currentPage = Math.floor((draftCards.length - 1) / pageSize);
      renderRows();
      rows.lastElementChild?.querySelector(".wl-term")?.focus();
    } },
      icon("folder-plus", "sm"), t("wordlist_add_row"));

    const typingBtn = el("button", { class: "btn primary lg", disabled: wl.cards.length === 0,
      onclick: () => startPractice("typing") }, icon("pencil", "sm"), t("wordlist_practice_typing"));
    const flashcardBtn = el("button", { class: "btn ghost lg", disabled: wl.cards.length === 0,
      onclick: () => startPractice("flashcards") }, icon("cards", "sm"), t("wordlist_practice_flashcards"));
    const directionBtn = el("button", { class: "btn ghost wl-direction-button", type: "button" });
    const syncDirectionButton = () => {
      const label = practiceDirection === "term_first"
        ? t("wordlist_direction_term_definition") : t("wordlist_direction_definition_term");
      directionBtn.replaceChildren(icon("flip", "sm"), label);
      directionBtn.setAttribute("aria-label", `${t("wordlist_direction")}: ${label}`);
    };
    directionBtn.addEventListener("click", () => {
      practiceDirection = practiceDirection === "term_first" ? "definition_first" : "term_first";
      savePrefs({ wordlistDirection: practiceDirection });
      syncDirectionButton();
    });
    syncDirectionButton();

    const inner = el("div", { class: "content-inner narrow" },
      el("h1", { class: "page-title" }, icon("book"), wl.name),
      el("p", { class: "page-sub" }, t("wordlist_terms") + `: ${wl.cards.length}` + (dueCount ? ` · ${dueCount} ${t("stat_due").toLowerCase()}` : "")),
      el("div", { class: "wl-practice-options" },
        el("span", { class: "wl-practice-label" }, t("wordlist_choose_practice")),
        el("div", { class: "wl-practice-buttons" }, typingBtn, flashcardBtn),
        el("div", { class: "wl-direction-row" },
          el("span", {}, t("wordlist_direction")), directionBtn)),
      el("div", { class: "wl-editor" },
        el("div", { class: "wl-head", "aria-hidden": "true" },
          el("span", {}, t("side_term")), el("span", {}, t("side_def")), el("span")),
        rows, pager, addBtn),
    );
    main.replaceChildren(el("div", { class: "content-page" }, inner));

    async function startPractice(mode) {
      // eerst opslaan wat er nog niet gesynct is, dan de verse lijst oefenen
      const cards = draftCards.map(c => ({
        term: (c.term || "").trim(), definition: (c.definition || "").trim(),
      })).filter(c => c.term && c.definition);
      try {
        wl = (await api.wordlistUpdate(wl.id, { cards })).wordlist;
      } catch (err) { toast(err.message, "err"); return; }
      const fresh = (await api.wordlistGet(wl.id)).wordlist;
      wl = fresh;
      const due = fresh.cards.filter(c => c.is_due);
      const practiceCards = due.length ? due : [...fresh.cards];
      if (mode === "typing") runTypingPractice(practiceCards, practiceDirection);
      else runPractice(practiceCards, practiceDirection);
    }
  }

  function runTypingPractice(cards, direction) {
    const page = el("div", { class: "content-page" });
    main.replaceChildren(page);
    const queue = makeTypingQueue(cards);
    const missed = new Set();
    const cardStates = new Map(cards.map(card => [card.id, {
      everWrong: false, savedAgain: false, finalQueued: false,
    }]));
    let index = 0;
    let busy = false;

    const saveRating = async (card, rating) => {
      busy = true;
      try {
        await api.wordlistReview(wl.id, { card_id: card.id, rating });
        return true;
      }
      catch (err) { toast(err.message, "err"); return false; }
      finally { busy = false; }
    };

    const finish = () => doneScreen(page,
      t("wordlist_typing_done", { n: cards.length, mistakes: missed.size }),
      () => renderWordlist(rootReset(), wl.id));

    const next = () => {
      index += 1;
      if (index >= queue.length) finish();
      else show();
    };

    const show = () => {
      const entry = queue[index];
      const card = entry.card;
      const sides = wordlistSides(card, direction);
      const input = el("input", {
        class: "field wl-type-input", autocomplete: "off", autocapitalize: "none",
        placeholder: t("wordlist_type_placeholder"), "aria-label": t("wordlist_type_placeholder"),
      });
      const feedback = el("div", { class: "wl-type-feedback", hidden: true });
      const actions = el("div", { class: "wl-type-actions" });
      const check = el("button", { class: "btn primary lg" }, t("wordlist_check_answer"));
      const checkRow = el("div", { class: "wl-type-check" }, check);

      const continueAfter = async (accepted) => {
        if (busy) return;
        actions.querySelectorAll("button").forEach(button => { button.disabled = true; });
        const state = cardStates.get(card.id);
        if (accepted) {
          if (!state.everWrong) {
            if (!await saveRating(card, "good")) {
              actions.querySelectorAll("button").forEach(button => { button.disabled = false; });
              return;
            }
          }
          else if (entry.phase === "final") {
            // Pas na de afsluitende herhaling is een eerder fout woord klaar.
            if (!await saveRating(card, "hard")) {
              actions.querySelectorAll("button").forEach(button => { button.disabled = false; });
              return;
            }
          }
          else if (!state.finalQueued) {
            // Goed geworden na één of meer fouten: altijd nog eenmaal terug in
            // de gezamenlijke eindronde, ná alle gewone vragen en herkansingen.
            queue.push({ card, phase: "final" });
            state.finalQueued = true;
          }
        }
        else {
          state.everWrong = true;
          missed.add(card.id);
          if (!state.savedAgain) {
            if (!await saveRating(card, "again")) {
              actions.querySelectorAll("button").forEach(button => { button.disabled = false; });
              return;
            }
            state.savedAgain = true;
          }
          // Ook een tweede, derde, … fout antwoord blijft na twee andere
          // vragen terugkomen. In de eindronde blijft het een eindvraag.
          scheduleIncorrectCard(queue, index, card, entry.phase === "final" ? "final" : "retry");
        }
        next();
      };

      const revealCorrect = () => {
        input.disabled = true;
        checkRow.remove();
        feedback.hidden = false;
        feedback.className = "wl-type-feedback correct";
        feedback.replaceChildren(icon("check", "sm"), el("span", {}, t("wordlist_answer_correct")));
        actions.replaceChildren(el("button", {
          class: "btn primary lg", onclick: () => continueAfter(true),
        }, t("next")));
        actions.querySelector("button")?.focus();
      };

      const revealIncorrect = () => {
        input.disabled = true;
        checkRow.remove();
        feedback.hidden = false;
        feedback.className = "wl-type-feedback incorrect";
        feedback.replaceChildren(
          el("span", {}, t("wordlist_answer_was")),
          el("strong", {}, sides.answer),
        );
        actions.replaceChildren(
          el("button", { class: "btn ghost", onclick: () => continueAfter(true) }, t("wordlist_mark_correct")),
          el("button", { class: "btn primary", onclick: () => continueAfter(false) }, t("wordlist_mark_wrong")),
        );
        actions.lastElementChild?.focus();
      };

      const submit = () => {
        // Ook een leeg antwoord mag gecontroleerd worden: dat is een gewone
        // fout en toont meteen het juiste antwoord plus de twee zelfkeuzes.
        if (input.disabled || busy) return;
        if (isTypedAnswerCorrect(input.value, sides.answer)) revealCorrect();
        else revealIncorrect();
      };
      check.onclick = submit;
      input.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); submit(); }
      });

      const phaseLabel = entry.phase === "final"
        ? t("wordlist_final_round")
        : entry.phase === "retry" ? t("wordlist_retry_round") : t("wordlist_type_term");
      page.replaceChildren(el("div", { class: "content-inner narrow wl-type-session" },
        el("div", { class: "quiz-top" },
          el("div", { class: "progress-track" },
            el("div", { class: "progress-fill", style: `width:${(index / Math.max(queue.length, 1)) * 100}%` })),
          el("span", { class: "count" }, `${index + 1} / ${queue.length}`)),
        el("section", { class: "wl-type-card" },
          el("span", { class: "side-lbl" }, phaseLabel),
          el("div", { class: "wl-type-prompt" }, sides.prompt),
          input,
          feedback,
          checkRow,
          actions),
      ));
      input.focus();
    };

    if (queue.length) show(); else finish();
  }

  function runPractice(queue, direction) {
    const page = el("div", { class: "content-page" });
    main.replaceChildren(page);
    const termFirst = direction !== "definition_first";
    runReviewSession(page, {
      cards: queue,
      labelQ: t(termFirst ? "side_term" : "side_def"),
      labelA: t(termFirst ? "side_def" : "side_term"),
      front: (c) => wordlistSides(c, direction).prompt,
      back: (c) => wordlistSides(c, direction).answer,
      onRate: async (card, rating) => { await api.wordlistReview(wl.id, { card_id: card.id, rating }); },
      onDone: (container, reviewed, total) => doneScreen(container, t("reviewed_n", { n: reviewed }), () => renderWordlist(rootReset(), wl.id)),
    });
  }

  // Na een sessie de lijst vers herladen (voortgang bijgewerkt).
  function rootReset() { root.replaceChildren(); return root; }

  async function remove(list) {
    const ok = await confirmDialog({ title: t("wordlist_del_t"), body: t("wordlist_del_b", { name: list.name }) });
    if (!ok) return;
    try { await api.wordlistDelete(list.id); toast(t("deleted"), "ok"); navigate("#/"); }
    catch (err) { toast(err.message, "err"); }
  }

  function exportList(list) {
    const clean = (s) => String(s || "").replace(/\t/g, " ").replace(/\n/g, "<br>");
    const tsv = list.cards.map(c => `${clean(c.term)}\t${clean(c.definition)}`).join("\n");
    downloadText(`${list.name} - woordenlijst.txt`, "text/tab-separated-values", tsv);
    toast(t("anki_done"), "ok", 4500);
  }
}
