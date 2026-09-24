// Opgaven: koppel een opgavenblad/oefententamen aan één college óf aan een heel
// vak (map), en laat de app je helpen. De kern: "Waar staat dit?" wijst de dia's
// aan waar je de theorie vindt, zodat je bij een lastige opgave even kunt
// terugkijken hoe het ook alweer zat. Plus begeleidende hints (geen kant-en-klaar
// antwoord, tenzij je erom vraagt). De opgave wordt via vision in losse
// (deel)vragen gesplitst, zodat alles per vraag werkt.
//
// scope = { sourceHash, folderId, name }:
//  - college:  { sourceHash: <hash>, folderId: <map of null>, name }
//  - heel vak: { sourceHash: null,   folderId: <map>,         name }
import { api } from "../api.js";
import { el, icon, toast, confirmDialog } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { renderMarkdown, createStreamRenderer } from "../markdown.js";
import { navigate } from "../app.js";

const FILE_ACCEPT = "image/*,.pdf,.png,.jpg,.jpeg,.webp,.heic,.heif";

// Entree vanuit de studeer-workspace: opgaven bij dít college.
export function mountExercises(main, ctx) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  showList(page, { sourceHash: ctx.hash, folderId: ctx.doc.folder_id || null, name: ctx.doc.file_name }, false);
}

// Entree vanuit de mapweergave: opgaven bij het hele vak. Rendert in `container`.
export function mountExercisesInFolder(container, folder) {
  showList(container, { sourceHash: null, folderId: folder.id, name: folder.name }, true);
}

function shell(embedded, children) {
  return embedded ? el("div", {}, ...children) : el("div", { class: "content-inner narrow" }, ...children);
}

/* ---------- lijst + toevoegen ---------- */
// De kop van de Opgaven-tab: dezelfde dropzone-taal als het uploaden van een
// college op de homepagina (slepen, klikken of toetsenbord), plus een foto-knop
// — een opgavenblad is vaak papier. De dropzone ís de lege staat; een losse
// "nog niets hier"-tekst eronder zou hetzelfde twee keer zeggen.
function uploadPanel(scope, embedded, onDone) {
  const fileInput = el("input", { type: "file", accept: FILE_ACCEPT, style: "display:none" });
  // capture="environment" opent op mobiel meteen de achtercamera.
  const camInput = el("input", { type: "file", accept: "image/*", capture: "environment", style: "display:none" });

  const dz = el("div", { class: `dropzone${embedded ? " compact" : ""}`, role: "button", tabindex: "0",
                         "aria-label": t("add_exercise") },
    el("div", { class: "dz-icon" }, icon("doc", embedded ? "" : "lg")),
    el("h3", {}, t("exercises_drop_title")),
    el("p", {}, t("exercises_drop_sub")),
    embedded ? null : el("div", { class: "formats" },
      el("span", { class: "chip" }, "PDF"), el("span", { class: "chip" }, t("fmt_image"))),
  );

  const startUpload = async (file) => {
    if (!file) return;
    dz.replaceChildren(
      el("div", { class: "dz-icon" }, el("span", { class: "spinner" })),
      el("h3", {}, t("exercise_uploading")),
      el("p", {}, file.name),
    );
    try {
      await api.upload(file, null, "exercise", { sourceFileHash: scope.sourceHash, folderId: scope.folderId });
    } catch (err) {
      toast(err.message, "err", 5000);
    }
    onDone(); // hertekent de kop (en dus ook de dropzone) in rusttoestand
  };

  fileInput.addEventListener("change", () => startUpload(fileInput.files?.[0]));
  camInput.addEventListener("change", () => startUpload(camInput.files?.[0]));
  dz.addEventListener("click", () => fileInput.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("drag");
    startUpload(e.dataTransfer.files?.[0]);
  });

  return el("div", {}, dz, fileInput, camInput,
    el("div", { style: "display:flex;justify-content:center;margin-top:-4px" },
      el("button", { class: "btn ghost", style: "font-size:12.5px", onclick: () => camInput.click() },
        icon("image", "sm"), t("exercise_photo"))),
  );
}

async function showList(page, scope, embedded) {
  const listArea = el("div", { style: "margin-top:18px" });
  // Waar een nieuwe opgave aan gekoppeld wordt, hangt af van wáár je hem
  // toevoegt (dit college of het hele vak). Dat is onzichtbaar zolang je het
  // niet benoemt, dus staat het er gewoon: "Gekoppeld aan: <naam>".
  const scopeChip = el("div", { class: "scope-chip" },
    icon(scope.sourceHash ? "book" : "folder", "sm"),
    el("span", {}, t("linked_to"), ": "), el("strong", {}, scope.name || ""));
  const header = embedded
    ? [el("div", { class: "section-title", style: "margin-top:8px" }, icon("doc", "sm"), t("tab_exercises"), el("span", { class: "line" })),
       scopeChip]
    : [el("h1", { class: "page-title" }, icon("doc"), t("tab_exercises")),
       el("p", { class: "page-sub" }, t("exercises_sub")),
       scopeChip];
  page.replaceChildren(shell(embedded, [
    ...header,
    uploadPanel(scope, embedded, () => showList(page, scope, embedded)),
    listArea,
  ]));

  try {
    const data = scope.sourceHash ? await api.exercisesList(scope.sourceHash)
                                  : await api.exercisesInFolder(scope.folderId);
    renderList(listArea, page, scope, embedded, data.exercises || []);
  } catch (err) {
    listArea.replaceChildren(el("p", { style: "color:var(--muted);font-size:13px;margin-top:16px" }, err.message));
  }
}

function renderList(container, page, scope, embedded, exercises) {
  if (!exercises.length) { container.replaceChildren(); return; } // dropzone is al de lege staat
  container.replaceChildren(
    el("div", { class: "section-title" }, t("exercises_yours"), el("span", { class: "line" })),
    ...exercises.map(ex => el("div", { class: "plan-row", style: "cursor:pointer",
      onclick: () => openExercise(page, scope, embedded, ex) },
      el("span", { style: "color:var(--accent)" }, icon("doc")),
      el("div", { style: "flex:1;min-width:0" },
        el("div", { style: "font-weight:600;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" }, ex.file_name),
        el("div", { style: "font-size:12px;color:var(--muted);margin-top:2px" },
          (ex.total_pages || 1) === 1 ? t("n_page") : t("n_pages", { n: ex.total_pages }))),
      // Verwijderen moet hier kunnen: opgaven staan bewust niet in de gewone
      // documentenlijsten, dus dit is de enige plek waar je ze kwijt kunt.
      deleteButton(ex, () => showList(page, scope, embedded)),
      icon("right", "sm"),
    )),
  );
}

function deleteButton(ex, onDeleted) {
  const btn = el("button", { class: "btn ghost icon-btn", title: t("delete") }, icon("trash", "sm"));
  btn.addEventListener("click", async (e) => {
    e.stopPropagation(); // niet de opgave openen
    const ok = await confirmDialog({ title: t("exercise_del_t"), body: t("exercise_del_b", { name: ex.file_name }) });
    if (!ok) return;
    try {
      await api.deleteDocument(ex.file_hash);
      toast(t("deleted"), "ok");
      onDeleted();
    } catch (err) { toast(err.message, "err"); }
  });
  return btn;
}

/* ---------- één opgave doorwerken ---------- */
async function openExercise(page, scope, embedded, ex) {
  const back = el("button", { class: "btn ghost", style: "font-size:12.5px" }, icon("left", "sm"), t("exercises_back"));
  back.addEventListener("click", () => showList(page, scope, embedded));

  const body = el("div", {});
  page.replaceChildren(shell(embedded, [
    el("div", { style: "display:flex;align-items:center;gap:12px;margin-bottom:6px" },
      back, el("div", { style: "font-weight:700;font-size:16px" }, ex.file_name)),
    body,
  ]));
  body.replaceChildren(el("div", { style: "display:grid;place-items:center;padding:30px" }, el("div", { class: "spinner" })));

  let exDoc;
  try {
    exDoc = await api.getDocument(ex.file_hash);
  } catch (err) {
    body.replaceChildren(el("p", { style: "color:var(--muted)" }, err.message));
    return;
  }
  const pages = exDoc.pages || [];

  // Referentie-afbeelding van de opgave (pager bij meerdere pagina's).
  let imgIdx = 0;
  const imgEl = el("img", { class: "exercise-img", alt: "" });
  const counter = el("span", { class: "count" });
  const setImg = () => { imgEl.src = api.slideImageUrl(ex.file_hash, imgIdx); counter.textContent = `${imgIdx + 1} / ${pages.length}`; };
  const imgPager = pages.length > 1 ? el("div", { class: "quiz-top", style: "margin-bottom:10px" },
    el("button", { class: "btn ghost icon-btn", title: t("prev"), "aria-label": t("prev"),
      onclick: () => { imgIdx = (imgIdx - 1 + pages.length) % pages.length; setImg(); } }, icon("left", "sm")),
    counter,
    el("button", { class: "btn ghost icon-btn", title: t("next"), "aria-label": t("next"),
      onclick: () => { imgIdx = (imgIdx + 1) % pages.length; setImg(); } }, icon("right", "sm")),
  ) : null;
  setImg();

  // "Breder zoeken in het hele vak" is alleen zinvol bij een college dat in een
  // map zit; bij een vak-brede opgave is de scope al het hele vak.
  const canWiden = !!(scope.sourceHash && scope.folderId);
  const widenState = { on: false };
  const widenRow = canWiden ? el("label", { class: "widen-toggle" },
    el("input", { type: "checkbox", onchange: (e) => { widenState.on = e.target.checked; } }),
    t("search_wider")) : null;

  const questionsArea = el("div", { style: "margin-top:16px" });
  body.replaceChildren(
    imgPager,
    el("div", { class: "exercise-stage" }, imgEl),
    widenRow ? el("div", { style: "margin-top:12px" }, widenRow) : null,
    questionsArea,
  );

  // Opgave in losse (deel)vragen splitsen (vision). Lukt dat niet of levert het
  // niets op → val terug op één blok voor de hele opgave.
  questionsArea.replaceChildren(el("div", { class: "stream-status" },
    el("span", { class: "dots" }, el("i"), el("i"), el("i")), t("exercise_parsing")));
  let questions = [];
  try { questions = (await api.exerciseQuestions({ exercise_hash: ex.file_hash, language: prefs.language })).questions || []; }
  catch { /* stil: fallback hieronder */ }

  if (!questions.length) {
    questionsArea.replaceChildren(questionCard(scope, ex, () => imgIdx, widenState, null, t("whole_exercise")));
    return;
  }
  questionsArea.replaceChildren(
    el("div", { class: "section-title" }, t("exercise_questions_title"), el("span", { class: "line" })),
    ...questions.map(q => questionCard(scope, ex, () => imgIdx, widenState, q.text,
      `${q.number ? q.number + ". " : ""}${q.text}`)),
  );
}

// Eén vraag(kaart): de vraagtekst + "Waar staat dit?" en "Hulp", elk met een
// eigen resultaatvak. questionText=null → op de hele opgavepagina.
function questionCard(scope, ex, getPage, widenState, questionText, displayText) {
  const locateBtn = el("button", { class: "btn primary", style: "font-size:12.5px" }, icon("target", "sm"), t("locate_slides"));
  const helpBtn = el("button", { class: "btn", style: "font-size:12.5px" }, icon("sparkle", "sm"), t("exercise_help"));
  const resultArea = el("div", { style: "margin-top:12px" });
  locateBtn.addEventListener("click", () => runLocate(resultArea, scope, ex, getPage(), widenState.on, locateBtn, questionText));
  helpBtn.addEventListener("click", () => runHelp(resultArea, scope, ex, getPage(), widenState.on, helpBtn, questionText));
  return el("div", { class: "question-card" },
    el("div", { class: "question-text md", html: renderMarkdown(displayText).replace(/^<p>|<\/p>\s*$/g, "") }),
    el("div", { style: "display:flex;gap:9px;flex-wrap:wrap;margin-top:10px" }, locateBtn, helpBtn),
    resultArea,
  );
}

// "Waar staat dit?" → de bijbehorende dia's, met per dia een reden + spring-knop.
async function runLocate(area, scope, ex, pageIndex, widen, btn, questionText = null) {
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("locating"));
  try {
    const res = await api.exerciseLocate({
      exercise_hash: ex.file_hash, page_index: pageIndex, question_text: questionText,
      source_file_hash: scope.sourceHash, folder_id: scope.folderId,
      widen, language: prefs.language,
    });
    const slides = res.slides || [];
    const box = el("div", { class: "locate-box" },
      el("div", { class: "grade-head", style: "display:flex;align-items:center;justify-content:space-between" },
        el("span", {}, icon("target"), t("locate_title")),
        el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 8px", onclick: () => location.hash = location.hash.replace(/\/\w+$/, '') },
          icon("left", "sm"), t("exercises_back"))));
    if (!slides.length) {
      box.append(el("p", { style: "font-size:13px;color:var(--muted);margin-top:6px" },
        (scope.sourceHash && scope.folderId && !widen) ? t("locate_none_widen") : t("locate_none")));
    } else {
      for (const s of slides) {
        box.append(el("div", { class: "locate-slide" },
          el("img", { src: api.slideImageUrl(s.file_hash, s.page_index), alt: "", loading: "lazy" }),
          el("div", { style: "flex:1;min-width:0" },
            el("div", { style: "font-weight:600;font-size:13px" }, t("slide_n", { n: s.page_index + 1 })),
            s.why ? el("div", { style: "font-size:12.5px;color:var(--text-soft);margin-top:2px" }, s.why) : null),
          el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px",
            onclick: () => navigate(`#/doc/${s.file_hash}/study/${s.page_index}`) },
            icon("book", "sm"), t("view_slide", { n: s.page_index + 1 })),
        ));
      }
    }
    area.replaceChildren(box);
  } catch (err) {
    toast(err.message, "err", 5000);
  } finally {
    btn.disabled = false;
    btn.replaceChildren(icon("target", "sm"), t("locate_slides"));
  }
}

// Begeleidende hulp: optionele eigen vraag + streamend antwoord (hints, geen
// kant-en-klaar antwoord tenzij je erom vraagt).
function runHelp(area, scope, ex, pageIndex, widen, btn, questionText = null) {
  const input = el("textarea", { class: "field", rows: "2", placeholder: t("exercise_help_ph") });
  const askBtn = el("button", { class: "btn primary", style: "font-size:12.5px" }, icon("sparkle", "sm"), t("exercise_help_go"));
  const answer = el("div", { class: "md" });
  const box = el("div", { class: "help-box" },
    el("div", { class: "grade-head" }, icon("sparkle"), t("exercise_help")),
    input,
    el("div", { style: "margin-top:8px" }, askBtn),
    answer,
  );
  area.replaceChildren(box);

  let streaming = false;
  const go = () => {
    if (streaming) return;
    streaming = true;
    askBtn.disabled = true;
    askBtn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("thinking"));
    answer.replaceChildren();
    const renderer = createStreamRenderer(answer);
    const stop = () => { streaming = false; askBtn.disabled = false; askBtn.replaceChildren(icon("sparkle", "sm"), t("exercise_help_go")); };
    api.exerciseHelpStream({
      exercise_hash: ex.file_hash, page_index: pageIndex, question_text: questionText,
      source_file_hash: scope.sourceHash, folder_id: scope.folderId,
      widen, question: input.value.trim() || null, language: prefs.language,
    }, {
      onDelta(text) { renderer.append(text); },
      onDone() { renderer.finish(); stop(); },
      onError(err) { toast(err.message, "err", 5000); stop(); },
    });
  };
  askBtn.addEventListener("click", go);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); go(); } });
}
