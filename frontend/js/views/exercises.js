// Opgaven: koppel een opgavenblad/oefententamen aan één college óf aan een heel
// vak (map), en laat de app je helpen. De kern: "Waar staat dit?" wijst de dia's
// aan waar je de theorie vindt, zodat je bij een lastige opgave even kunt
// terugkijken hoe het ook alweer zat. Plus een volledige stap-voor-stapuitwerking.
// De opgave wordt via vision in losse
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
import { exerciseViewer } from "../exercise-viewer.js";
import { exerciseReturn, saveExerciseReturn, clearExerciseReturn } from "../exercise-navigation.js";

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
  page.classList.remove("exercise-open", "exercise-embedded");
  const returning = exerciseReturn();
  if (returning?.restore && returning.scope?.sourceHash === scope.sourceHash && returning.scope?.folderId === scope.folderId) {
    clearExerciseReturn();
    await openExercise(page, scope, embedded, returning.ex, returning);
    return;
  }
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
async function openExercise(page, scope, embedded, ex, returning = null) {
  const back = el("button", { class: "btn ghost", style: "font-size:12.5px" }, icon("left", "sm"), t("exercises_back"));
  back.addEventListener("click", () => showList(page, scope, embedded));

  page.classList.add("exercise-open");
  page.classList.toggle("exercise-embedded", embedded);
  const body = el("div", { class: "exercise-workspace" });
  page.replaceChildren(
    el("div", { class: "exercise-workspace-head" },
      back, el("div", { style: "font-weight:700;font-size:16px" }, ex.file_name)),
    body,
  );
  body.replaceChildren(el("div", { style: "display:grid;place-items:center;padding:30px" }, el("div", { class: "spinner" })));

  let exDoc;
  try {
    exDoc = await api.getDocument(ex.file_hash);
  } catch (err) {
    body.replaceChildren(el("p", { style: "color:var(--muted)" }, err.message));
    return;
  }
  const totalPages = Math.max(exDoc.pages?.length || 0, exDoc.total_pages || 0, ex.total_pages || 0, 1);

  // Referentie-afbeelding van de opgave (pager bij meerdere pagina's).
  const viewer = exerciseViewer(ex.file_hash, totalPages, returning?.pageIndex || 0);

  // "Breder zoeken in het hele vak" is alleen zinvol bij een college dat in een
  // map zit; bij een vak-brede opgave is de scope al het hele vak.
  const canWiden = !!(scope.sourceHash && scope.folderId);
  const widenState = { on: false };
  const widenRow = canWiden ? el("label", { class: "widen-toggle" },
    el("input", { type: "checkbox", onchange: (e) => { widenState.on = e.target.checked; } }),
    t("search_wider")) : null;

  const questionsArea = el("div", { class: "exercise-questions" });
  const panel = el("div", { class: "exercise-question-panel" },
    el("div", { class: "panel-head" }, el("strong", {}, t("exercise_questions_title")), widenRow), questionsArea);
  const divider = el("div", { class: "panel-divider", role: "separator", tabindex: "0", "aria-orientation": "vertical", "aria-label": t("exercise_resize") });
  body.replaceChildren(viewer.element, divider, panel);
  let customWidth = false;
  let pageRatio = 0.707;
  const setWidth = width => {
    const w = body.clientWidth;
    body.style.setProperty("--exercise-left", `${Math.max(280, Math.min(w - 340, width))}px`);
  };
  const autoWidth = () => {
    if (!page.isConnected) { layoutObserver.disconnect(); return; }
    if (!customWidth && body.clientWidth > 760) {
      const navHeight = viewer.element.querySelector(".stage-nav")?.offsetHeight || 56;
      setWidth((body.clientHeight - navHeight - 36) * pageRatio + 36);
    }
  };
  const layoutObserver = new ResizeObserver(autoWidth);
  layoutObserver.observe(body);
  viewer.element.addEventListener("exercise:page-size", event => { pageRatio = event.detail.ratio; autoWidth(); });
  divider.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    event.preventDefault(); divider.setPointerCapture(event.pointerId); customWidth = true;
    const move = e => setWidth(e.clientX - body.getBoundingClientRect().left);
    const end = () => {
      divider.removeEventListener("pointermove", move);
      divider.removeEventListener("pointerup", end);
      divider.removeEventListener("pointercancel", end);
    };
    divider.addEventListener("pointermove", move);
    divider.addEventListener("pointerup", end);
    divider.addEventListener("pointercancel", end);
  });
  divider.addEventListener("dblclick", () => { customWidth = false; autoWidth(); });
  divider.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Home") { customWidth = false; autoWidth(); }
    else { customWidth = true; setWidth(viewer.element.getBoundingClientRect().width + (event.key === "ArrowRight" ? 24 : -24)); }
  });

  // Opgave in losse (deel)vragen splitsen (vision). Lukt dat niet of levert het
  // niets op → val terug op één blok voor de hele opgave.
  questionsArea.replaceChildren(el("div", { class: "stream-status" },
    el("span", { class: "dots" }, el("i"), el("i"), el("i")), t("exercise_parsing")));
  let questions = [];
  try { questions = (await api.exerciseQuestions({ exercise_hash: ex.file_hash, language: prefs.language })).questions || []; }
  catch { /* stil: fallback hieronder */ }

  if (!questions.length) {
    questionsArea.replaceChildren(questionCard(scope, ex, viewer.getPage, widenState, null, t("whole_exercise")));
    return;
  }
  questionsArea.replaceChildren(
    ...questions.map(q => {
      const pageIndex = Number.isInteger(q.page_index) ? q.page_index : 0;
      return questionCard(scope, ex, () => pageIndex, widenState, q.text,
        `${q.number ? q.number + ". " : ""}${q.text}`,
        () => viewer.setPage(pageIndex),
        pageIndex);
    }),
  );
  if (returning) requestAnimationFrame(() => {
    const card = [...questionsArea.querySelectorAll(".question-card")].find(node => node.dataset.question === returning.questionText);
    questionsArea.scrollTop = card
      ? questionsArea.scrollTop + card.getBoundingClientRect().top - questionsArea.getBoundingClientRect().top - 12
      : returning.scrollTop || 0;
  });
}

// Eén vraag(kaart): de vraagtekst + "Waar staat dit?" en "Hulp", elk met een
// eigen resultaatvak. questionText=null → op de hele opgavepagina.
function questionCard(scope, ex, getPage, widenState, questionText, displayText, showPage = null, fixedPage = null) {
  const locateBtn = el("button", { class: "btn primary", style: "font-size:12.5px" }, icon("target", "sm"), t("locate_slides"));
  const helpBtn = el("button", { class: "btn", style: "font-size:12.5px" }, icon("sparkle", "sm"), t("exercise_help"));
  helpBtn.textContent = t("exercise_show_answer");
  const hintBtn = el("button", { class: "btn", style: "font-size:12.5px" }, icon("sparkle", "sm"), t("exercise_hint"));
  const stepsBtn = el("button", { class: "btn", style: "font-size:12.5px" }, t("exercise_steps"));
  const resultArea = el("div", { style: "margin-top:12px" });
  const hintArea = el("div", {});
  const stepsArea = el("div", {});
  const answerArea = el("div", {});
  const solutions = new Map();
  const solution = (mode, area, btn) => {
    showPage?.();
    const key = `${mode}:${getPage()}:${widenState.on}:${prefs.language}`;
    const saved = solutions.get(key);
    if (saved) { area.replaceChildren(saved); return; }
    const box = runSolution(area, scope, ex, getPage(), widenState.on, btn, questionText, mode);
    solutions.set(key, box);
    box.addEventListener("exercise:failed", () => solutions.delete(key), { once: true });
  };
  locateBtn.addEventListener("click", async () => {
    showPage?.();
    const key = `locate:${getPage()}:${widenState.on}:${prefs.language}`;
    if (solutions.has(key)) { resultArea.replaceChildren(solutions.get(key)); return; }
    const box = await runLocate(resultArea, scope, ex, getPage(), widenState.on, locateBtn, questionText);
    if (box) solutions.set(key, box);
  });
  helpBtn.addEventListener("click", () => solution("answer", answerArea, helpBtn));
  hintBtn.addEventListener("click", () => solution("hint", hintArea, hintBtn));
  stepsBtn.addEventListener("click", () => solution("steps", stepsArea, stepsBtn));
  return el("div", { class: "question-card", dataset: { question: questionText || "" } },
    fixedPage !== null ? el("div", { class: "exercise-page-chip" }, icon("doc", "sm"), t("exercise_page", { n: fixedPage + 1 })) : null,
    el("div", { class: "question-text md", html: renderMarkdown(displayText).replace(/^<p>|<\/p>\s*$/g, "") }),
    el("div", { class: "exercise-question-actions" }, hintBtn, stepsBtn, helpBtn, locateBtn),
    hintArea, stepsArea, answerArea, resultArea,
  );
}

function runSolution(area, scope, ex, pageIndex, widen, btn, questionText, mode) {
  const label = t(mode === "hint" ? "exercise_hint" : mode === "steps" ? "exercise_steps" : "exercise_show_answer");
  const answer = el("div", { class: "md", "aria-live": "polite" });
  const box = el("div", { class: "help-box exercise-solution" },
    el("div", { class: "grade-head" }, icon("sparkle"), label), answer);
  area.replaceChildren(box);
  btn.disabled = true;
  btn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("thinking"));
  const renderer = createStreamRenderer(answer);
  const stop = () => { btn.disabled = false; btn.textContent = label; };
  api.exerciseHelpStream({
    exercise_hash: ex.file_hash, page_index: pageIndex, question_text: questionText,
    source_file_hash: scope.sourceHash, folder_id: scope.folderId,
    widen, language: prefs.language, mode,
  }, {
    onDelta(text) { renderer.append(text); },
    onDone() { renderer.finish(); stop(); },
    onError(err) { answer.textContent = err.message; box.dispatchEvent(new Event("exercise:failed")); stop(); },
  });
  return box;
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
      el("div", { class: "grade-head" }, icon("target"), t("locate_title")));
    if (!slides.length) {
      box.append(el("p", { style: "font-size:13px;color:var(--muted);margin-top:6px" },
        (scope.sourceHash && scope.folderId && !widen) ? t("locate_none_widen") : t("locate_none")));
    } else {
      for (const s of slides) {
        const thumb = el("img", { alt: "", loading: "lazy" });
        api.setSlideImage(thumb, s.file_hash, s.page_index).catch(() => {});
        const preview = el("button", { class: "exercise-preview-thumb", "aria-label": t("exercise_preview"), title: t("exercise_preview"),
          onclick: () => previewSource(s) }, thumb, el("span", {}, "+"));
        box.append(el("div", { class: "locate-slide" },
          preview,
          el("div", { style: "flex:1;min-width:0" },
            el("div", { style: "font-weight:600;font-size:13px" },
              s.file_name ? `${s.file_name} · ` : "", t("slide_n", { n: s.page_index + 1 })),
            s.why ? el("div", { style: "font-size:12.5px;color:var(--text-soft);margin-top:2px" }, s.why) : null),
          el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px",
            onclick: () => {
              saveExerciseReturn({ scope, ex, pageIndex, questionText, sourceHash: s.file_hash, url: location.hash,
                scrollTop: area.closest(".exercise-questions")?.scrollTop || 0, restore: false });
              navigate(`#/doc/${s.file_hash}/study/${s.page_index}`);
            } },
            icon("book", "sm"), t("view_slide", { n: s.page_index + 1 })),
        ));
      }
    }
    area.replaceChildren(box);
    return box;
  } catch (err) {
    area.replaceChildren(el("div", { class: "inline-error" }, icon("warning", "sm"), err.message));
  } finally {
    btn.disabled = false;
    btn.replaceChildren(icon("target", "sm"), t("locate_slides"));
  }
}

async function previewSource(slide) {
  const dialog = el("dialog", { class: "exercise-dialog", "aria-label": t("exercise_preview") });
  const close = el("button", { class: "btn", onclick: () => dialog.close() }, t("close"));
  const content = el("div", { style: "flex:1;min-height:0" }, el("div", { class: "spinner" }));
  dialog.append(el("div", { class: "exercise-workspace-head" }, el("strong", {}, `${slide.file_name} · ${t("slide_n", { n: slide.page_index + 1 })}`), close), content);
  dialog.style.display = "flex"; dialog.style.flexDirection = "column";
  document.body.append(dialog); dialog.addEventListener("close", () => dialog.remove(), { once: true }); dialog.showModal();
  try {
    const doc = await api.getDocument(slide.file_hash);
    if (!dialog.isConnected) return;
    const viewer = exerciseViewer(slide.file_hash, doc.total_pages || slide.page_index + 1, slide.page_index);
    content.className = "exercise-source-preview";
    content.replaceChildren(viewer.element);
  } catch (err) { content.textContent = err.message; }
}

// Volledige hulp: optionele eigen vraag + streamende stap-voor-stapuitwerking.
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
