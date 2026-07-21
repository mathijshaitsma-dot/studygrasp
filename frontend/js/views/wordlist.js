// Woordenlijst: bewerkbare term/definitie-rijen + oefenen met spaced repetition
// (dezelfde review-runner als flashcards). Een woordenlijst staat los van
// documenten en is bereikbaar via de home-sectie "Mijn woordenlijsten".
import { api } from "../api.js";
import { el, icon, toast, confirmDialog, debounce } from "../util.js";
import { t } from "../i18n.js";
import { navigate } from "../app.js";
import { runReviewSession, doneScreen } from "../review.js";
import { downloadText } from "../export.js";

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
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("book")), ""),
    el("div", { class: "doc-name", title: wl.name }, wl.name),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("export_anki"), onclick: () => exportList(wl) }, icon("download")),
    el("button", { class: "btn ghost icon-btn danger", title: t("delete"), onclick: () => remove(wl) }, icon("trash")),
  );
  const main = el("div", { class: "workspace", style: "overflow:auto" });
  root.append(topbar, main);

  renderEditor();

  function renderEditor() {
    const dueCount = wl.cards.filter(c => c.is_due).length;
    const rows = el("div", { class: "wl-rows" });

    const collectCards = () => [...rows.querySelectorAll(".wl-row")].map(r => ({
      term: r.querySelector(".wl-term").value.trim(),
      definition: r.querySelector(".wl-def").value.trim(),
    }));

    // debounced opslaan van de hele lijst zodra je typt/toevoegt/verwijdert.
    // Bewust vóór de rijen gedefinieerd: rowEl gebruikt 'save' meteen.
    const save = debounce(async () => {
      const cards = collectCards().filter(c => c.term || c.definition);
      try { wl = (await api.wordlistUpdate(wl.id, { cards })).wordlist; }
      catch (err) { toast(err.message, "err"); }
    }, 700);

    const rowEl = (c) => {
      const term = el("input", { class: "field wl-term", value: c.term || "", placeholder: t("wordlist_term_ph") });
      const def = el("input", { class: "field wl-def", value: c.definition || "", placeholder: t("wordlist_def_ph") });
      term.addEventListener("input", save);
      def.addEventListener("input", save);
      const del = el("button", { class: "btn ghost icon-btn", title: t("delete"), onclick: () => { row.remove(); save(); } }, icon("x", "sm"));
      const row = el("div", { class: "wl-row" }, term, def, del);
      return row;
    };

    wl.cards.forEach((c) => rows.append(rowEl(c)));

    const addBtn = el("button", { class: "btn ghost", onclick: () => { rows.append(rowEl({ term: "", definition: "", id: null })); } },
      icon("folder-plus", "sm"), t("wordlist_add_row"));

    const practiceBtn = el("button", { class: "btn primary lg", disabled: wl.cards.length === 0,
      onclick: () => startPractice() }, icon("play", "sm"), t("wordlist_practice"));

    const inner = el("div", { class: "content-inner narrow" },
      el("h1", { class: "page-title" }, icon("book"), wl.name),
      el("p", { class: "page-sub" }, t("wordlist_terms") + `: ${wl.cards.length}` + (dueCount ? ` · ${dueCount} ${t("stat_due").toLowerCase()}` : "")),
      el("div", { style: "display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px" }, practiceBtn),
      el("div", { class: "wl-editor" }, rows, addBtn),
    );
    main.replaceChildren(el("div", { class: "content-page" }, inner));

    async function startPractice() {
      // eerst opslaan wat er nog niet gesynct is, dan de verse lijst oefenen
      const cards = [...rows.querySelectorAll(".wl-row")].map(r => ({
        term: r.querySelector(".wl-term").value.trim(),
        definition: r.querySelector(".wl-def").value.trim(),
      })).filter(c => c.term && c.definition);
      try {
        wl = (await api.wordlistUpdate(wl.id, { cards })).wordlist;
      } catch (err) { toast(err.message, "err"); return; }
      const fresh = (await api.wordlistGet(wl.id)).wordlist;
      wl = fresh;
      const due = fresh.cards.filter(c => c.is_due);
      runPractice(due.length ? due : [...fresh.cards]);
    }
  }

  function runPractice(queue) {
    const page = el("div", { class: "content-page" });
    main.replaceChildren(page);
    runReviewSession(page, {
      cards: queue,
      labelQ: t("side_term"),
      labelA: t("side_def"),
      front: (c) => c.term,
      back: (c) => c.definition,
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
