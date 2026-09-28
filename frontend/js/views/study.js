// Werkruimte: topbar met tabs + studeer-tab (dia links, AI-uitleg rechts).
import { api } from "../api.js";
import { el, icon, brandMark, toast, debounce, openModal } from "../util.js";
import { prefs, savePrefs, explainKey, getCachedExplain, setCachedExplain, getChat } from "../state.js";
import { createStreamRenderer, renderMarkdown, renderCharts } from "../markdown.js";
import { study } from "../stats.js";
import { speak, stopSpeech, ttsSupported, prewarmSpeech } from "../tts.js";

// Sessievlaggen: zodra de gebruiker Kernpunten of voorlezen éénmaal gebruikt,
// warmen we die vast voor de dia's die hij daarna bekijkt — dan is het instant
// i.p.v. seconden laden. Wie een functie nooit gebruikt, betaalt er niets voor.
let usedKeypoints = false;
let usedTTS = false;
import { t, uiLocale } from "../i18n.js";
import { openSearch } from "../search.js";
import { openSettings, navigate, setFocusMode, toggleFocusMode } from "../app.js";
import { mountSummary } from "./summary.js";
import { mountQuiz } from "./quiz.js";
import { mountFlashcards } from "./flashcards.js";
import { mountExam } from "./exam.js";
import { mountExercises } from "./exercises.js";

// Voer `fn` pas uit na een korte hover-rust (~250ms), zodat een muis die lángs een
// knop veegt geen (dure) generatie afvuurt. Verlaat de knop binnen die tijd → niets.
// Toetsenbord-focus is bewuste intentie en gaat wél meteen. Hangt de listeners
// direct op `node`.
const HOVER_INTENT_MS = 250;
function onIntent(node, fn) {
  let timer = 0;
  node.addEventListener("mouseenter", () => { clearTimeout(timer); timer = setTimeout(fn, HOVER_INTENT_MS); });
  node.addEventListener("mouseleave", () => clearTimeout(timer));
  node.addEventListener("focus", fn);
}

export async function renderWorkspace(root, fileHash, tab = "study", pageOverride = null) {
  // ---- document laden ----
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);

  let doc;
  try {
    doc = await api.getDocument(fileHash);
  } catch (err) {
    loading.remove();
    root.append(el("div", { style: "flex:1;display:grid;place-items:center;padding:24px" },
      el("div", { class: "empty-state", style: "max-width:420px" },
        el("p", { style: "font-weight:600;color:var(--text)" }, t("doc_not_found")),
        el("p", { style: "font-size:13px" }, err.message),
        el("button", { class: "btn primary", onclick: () => navigate("#/") }, icon("home", "sm"), t("to_home")),
      )));
    return;
  }
  loading.remove();

  const ctx = {
    doc,
    hash: fileHash,
    page: pageOverride != null && pageOverride >= 0 && pageOverride < doc.total_pages
      ? pageOverride
      : Math.min(doc.last_page_index || 0, doc.total_pages - 1),
    mode: "explain",
  };

  // ---- topbar ----
  // Elke tab krijgt naast het label een korte omschrijving van wat hij dóet.
  // Nodig omdat de labels op smalle schermen verborgen worden (alleen icoon):
  // zonder title/aria-label zijn het daar zes naamloze icoontjes. Meteen lost
  // dit ook op dat "Overhoren" en "Tentamen" op elkaar lijken.
  const tabsDef = [
    ["study", "book", t("tab_study"), t("tab_study_tip")],
    ["summary", "summary", t("tab_summary"), t("tab_summary_tip")],
    ["quiz", "quiz", t("tab_quiz"), t("tab_quiz_tip")],
    ["exam", "cap", t("tab_exam"), t("tab_exam_tip")],
    ["exercises", "doc", t("tab_exercises"), t("tab_exercises_tip")],
    ["cards", "cards", t("tab_cards"), t("tab_cards_tip")],
  ];
  const main = el("div", { class: "workspace" });
  const tabBtns = {};
  const cardsBadge = el("span", { class: "badge", style: "display:none" });

  const setTab = (tKey) => {
    tab = tKey;
    history.replaceState(null, "", `#/doc/${fileHash}/${tKey}${tKey === "study" ? `/${ctx.page}` : ""}`);
    for (const [key, btn] of Object.entries(tabBtns)) btn.classList.toggle("on", key === tKey);
    // Op smalle schermen scrollt de tabbalk horizontaal; zonder dit staat de
    // actieve tab soms buiten beeld (bv. direct openen op #/doc/x/exercises).
    // block:"nearest" houdt de pagina zelf stil.
    tabBtns[tKey]?.scrollIntoView({ inline: "center", block: "nearest" });
    setFocusMode(false);
    stopSpeech();
    main.replaceChildren();
    if (tKey === "study") mountStudy(main, ctx);
    else if (tKey === "summary") mountSummary(main, ctx);
    else if (tKey === "quiz") mountQuiz(main, ctx);
    else if (tKey === "exam") mountExam(main, { scope: { file_hash: fileHash }, name: doc.file_name });
    else if (tKey === "exercises") mountExercises(main, ctx);
    else if (tKey === "cards") mountFlashcards(main, ctx, cardsBadge);
  };

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    brandMark(false),
    el("div", { class: "doc-name", title: doc.file_name }, doc.file_name),
    el("div", { class: "spacer" }),
    el("div", { class: "tabs" },
      ...tabsDef.map(([key, ic, label, tip]) => {
        const b = el("button", { class: key === tab ? "on" : "", title: `${label} — ${tip}`,
                                 "aria-label": `${label} — ${tip}`, onclick: () => setTab(key) },
          icon(ic, "sm"), el("span", { class: "lbl" }, label), key === "cards" ? cardsBadge : null);
        tabBtns[key] = b;
        return b;
      }),
    ),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("tip_search"), onclick: () => openSearch({ fileHash, onPick: (h) => { if (h.file_hash === fileHash) { ctx.page = h.page_index; setTab("study"); } else navigate(`#/doc/${h.file_hash}/study/${h.page_index}`); }, onHover: (h) => { if (h.file_hash === fileHash) warmVariant({ page: h.page_index }); } }) }, icon("search")),
    el("button", { class: "btn ghost icon-btn", title: t("tip_focus"), onclick: toggleFocusMode }, icon("focus")),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );

  // Klein rond kruisje — bewust geen timer of tekst, dat leidt af.
  const focusExit = el("button", { class: "focus-exit", title: t("exit_focus"), onclick: () => setFocusMode(false) },
    icon("x"));

  root.append(topbar, main, focusExit);

  // flashcards-badge alvast vullen (hoeveel kaarten zijn nu 'due', in de huidige taal)
  api.flashcardsGet(fileHash, prefs.language).then((data) => {
    if (data?.due_count > 0) { cardsBadge.textContent = data.due_count; cardsBadge.style.display = ""; }
  }).catch(() => {});

  // notities/markeringen die op een ander apparaat zijn gezet erbij halen
  api.notesGet(fileHash).then((data) => study.hydrate(fileHash, data?.pages)).catch(() => {});

  setTab(tab);
}

/* =====================================================================
   STUDEER-TAB
   ===================================================================== */
function mountStudy(main, ctx) {
  const { doc, hash } = ctx;
  let activeAbort = null;   // lopende uitleg-stream
  let chatAbort = null;     // lopende chat/regio-stream
  let attachPreviewUrl = "";

  /* ---------- linkerkant: dia ---------- */
  const slideImg = el("img", { alt: `${t("slide_n", { n: ctx.page + 1 })}`, draggable: "false" });
  const slideFrame = el("div", { class: "slide-frame" }, slideImg);
  const spinner = el("div", { class: "slide-spinner", style: "display:none" }, el("div", { class: "spinner" }));
  slideFrame.append(spinner);
  const slideHolder = el("div", { class: "slide-holder" }, slideFrame);

  // -- navigatie-elementen --
  // data-tip is puur de visuele tooltip (CSS); aria-label geeft de knop ook een
  // échte naam — zonder dat zijn dit voor een screenreader naamloze knoppen, en
  // op smalle schermen verdwijnt bij prev/next ook nog het zichtbare label.
  const regionBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_region"), "aria-label": t("tip_region") }, icon("crop", "sm"));
  const overviewBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_overview"), "aria-label": t("tip_overview") }, icon("grid", "sm"));

  const pageLabel = el("span", { class: "page-label" });
  const prevBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_prev"), "aria-label": t("tip_prev") }, icon("left", "sm"), el("span", { class: "lbl" }, t("prev")));
  const nextBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_next"), "aria-label": t("tip_next") }, el("span", { class: "lbl" }, t("next")), icon("right", "sm"));

  // dun voortgangsbalkje: hoe ver je in het document bent
  const progFill = el("div", { class: "progress-fill" });
  const progTrack = el("div", { class: "progress-track stage-progress" }, progFill);

  const stage = el("div", { class: "stage" },
    slideHolder,
    el("div", { class: "stage-nav" },
      el("div", { class: "tools" }, regionBtn, overviewBtn),
      el("div", { class: "spacer" }),
      prevBtn, pageLabel, nextBtn,
      el("div", { class: "spacer" }),
      progTrack,
    ),
  );

  /* ---------- rechterkant: uitleg-panel ---------- */
  // "Simpel" is bewust geen aparte modus meer: dat overlapt met niveau
  // "Beginner". Een extra-simpele uitleg zit als snelle actie onder de uitleg.
  const modeSeg = el("div", { class: "seg" });
  const modes = [["explain", t("mode_explain")], ["study", t("mode_keypoints")]];
  for (const [val, label] of modes) {
    const b = el("button", { class: val === ctx.mode ? "on" : "", onclick: (e) => {
      ctx.mode = val;
      if (val === "study") usedKeypoints = true;  // vanaf nu Kernpunten vast warmen
      modeSeg.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      e.currentTarget.classList.add("on");
      loadExplanation();
    } }, label);
    // Hover/focus = intentie om te schakelen → warm die modus alvast, dan is de klik instant.
    onIntent(b, () => warmVariant({ mode: val }));
    modeSeg.append(b);
  }

  const audSeg = el("div", { class: "seg subtle" });
  const audiences = [["beginner", t("lvl_beginner")], ["intermediate", t("lvl_mid")], ["advanced", t("lvl_adv")]];
  for (const [val, label] of audiences) {
    const b = el("button", { class: val === prefs.audienceLevel ? "on" : "", onclick: (e) => {
      savePrefs({ audienceLevel: val });
      audSeg.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      e.currentTarget.classList.add("on");
      loadExplanation();
    } }, label);
    // Hover/focus = intentie om van niveau te wisselen → warm dat niveau alvast.
    onIntent(b, () => warmVariant({ audienceLevel: val }));
    audSeg.append(b);
  }

  const noteBtn = el("button", { class: "btn ghost icon-btn", title: t("tip_note") }, icon("pencil"));
  const refreshBtn = el("button", { class: "btn ghost icon-btn", title: t("tip_refresh"), onclick: () => loadExplanation(true) }, icon("refresh"));

  // Notitie + markeringen zitten samen achter het potlood: zo blijft het
  // studeerscherm rustig, maar is alles over "deze dia" op één vindbare plek.
  const noteArea = el("textarea", { rows: "3", placeholder: t("note_ph") });
  const noteLabel = el("label", {}, "");
  const starChip = el("button", { class: "flag-chip star" }, icon("star", "sm"), t("flag_star"));
  const unclearChip = el("button", { class: "flag-chip unclear" }, icon("quiz", "sm"), t("flag_unclear"));
  starChip.addEventListener("click", () => toggleFlag("star"));
  unclearChip.addEventListener("click", () => toggleFlag("unclear"));
  const noteBox = el("div", { class: "note-box", style: "display:none" },
    noteLabel, noteArea,
    el("div", { class: "flag-row" }, starChip, unclearChip),
  );
  const saveNote = debounce(() => {
    study.setNote(hash, ctx.page, noteArea.value);
    refreshNoteButton();
  }, 400);
  noteArea.addEventListener("input", saveNote);
  noteBtn.addEventListener("click", () => {
    const show = noteBox.style.display === "none";
    noteBox.style.display = show ? "" : "none";
    noteBtn.classList.toggle("on", show);
    if (show) noteArea.focus();
  });

  const explainBox = el("div", {});   // hoofduitleg
  const chatBox = el("div", {});      // vervolgvragen
  const panelBody = el("div", { class: "panel-body" }, noteBox, explainBox, chatBox);

  // ---- tekstgrootte van de uitleg ----
  // Als CSS-variabele op het paneel, zodat alleen de uitleg meeschaalt en niet
  // de hele interface. De keuze wordt onthouden (prefs), want wie grotere tekst
  // nodig heeft, wil dat elke sessie.
  // ---- zoomen (uitleg én dia) ----
  // Twee dingen maken zoomen "smooth" in plaats van schokkerig:
  //
  // 1. VERMENIGVULDIGEND i.p.v. in vaste stapjes. Van 1,0 naar 1,07 voelt
  //    hetzelfde als van 2,0 naar 2,14, dus zoomt het overal even snel. En het
  //    reageert op ELK wheel-event: een voorzichtig duwtje geeft een klein beetje,
  //    een stevige haal geeft meer. Een drempel ("pas iets doen na 400 scroll")
  //    gaf juist het gevoel dat er eerst niets gebeurde en daarna een sprong.
  //
  // 2. deltaMode NORMALISEREN. Niet elke muis/browser stuurt pixels: sommige
  //    sturen regels (deltaMode 1, ~3 per klikje) of pagina's (deltaMode 2).
  //    Zonder omrekening had je op zo'n apparaat tientallen scrolls nodig voor één
  //    stapje — precies het "er gebeurt niets"-gevoel.
  // De uitleg zoomt fijn (je leest mee en wilt kleine correcties); op de dia wil
  // je juist snel dichtbij een grafiekje of voetnoot, dus die stapt ruimer.
  const TEXT_PER_NOTCH = 0.075;             // ≈ 7,8% per muiswiel-klikje
  const SLIDE_PER_NOTCH = 0.28;             // ≈ 32% per muiswiel-klikje
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  function wheelZoomFactor(e, perNotch) {
    // deltaMode: 0 = pixels, 1 = regels, 2 = pagina's → alles naar pixels.
    const perUnit = e.deltaMode === 1 ? 24 : e.deltaMode === 2 ? 200 : 1;
    const notches = (e.deltaY * perUnit) / 100;   // één muiswiel-klikje ≈ 100px
    return Math.exp(-notches * perNotch);
  }

  const EXPLAIN_MIN = 0.8, EXPLAIN_MAX = 1.8;
  const applyScale = () => {
    panelBody.style.setProperty("--explain-scale", String(prefs.explainScale || 1));
  };
  applyScale();

  // Ctrl/Cmd + scrollwiel boven de uitleg schaalt de tekst i.p.v. de browser in
  // te zoomen. Alleen binnen dit paneel, zodat browserzoom elders gewoon werkt.
  panelBody.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const next = clamp((prefs.explainScale || 1) * wheelZoomFactor(e, TEXT_PER_NOTCH), EXPLAIN_MIN, EXPLAIN_MAX);
    savePrefs({ explainScale: Math.round(next * 1000) / 1000 });
    applyScale();
  }, { passive: false });

  // Scrollen gebeurt altijd binnen het panel zelf — nooit via scrollIntoView,
  // want dat scrolt óók het document mee en dan verspringt de hele app.
  function scrollChatTo(node) {
    panelBody.scrollTo({ top: Math.max(0, node.offsetTop - 12), behavior: "smooth" });
  }
  function scrollChatBottom() {
    panelBody.scrollTop = panelBody.scrollHeight;
  }

  const askInput = el("textarea", { placeholder: t("ask_ph"), rows: "1" });
  const sendBtn = el("button", { class: "send", title: t("tip_send") }, icon("send", "sm"));
  askInput.addEventListener("input", () => {
    askInput.style.height = "auto";
    askInput.style.height = Math.min(askInput.scrollHeight, 130) + "px";
  });
  askInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitQuestion(); }
  });
  sendBtn.addEventListener("click", () => submitQuestion());

  // Spraak-invoer: vraag inspreken i.p.v. typen (waar de browser het kan).
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let micBtn = null;
  if (SR) {
    micBtn = el("button", { class: "mic tip", "data-tip": t("tip_mic"), "aria-label": t("tip_mic") }, icon("mic", "sm"));
    let rec = null;
    micBtn.addEventListener("click", () => {
      if (rec) { rec.stop(); return; }
      rec = new SR();
      rec.lang = uiLocale();
      rec.interimResults = true;
      const base = askInput.value ? askInput.value.replace(/\s+$/, "") + " " : "";
      rec.onresult = (e) => {
        let txt = "";
        for (const r of e.results) txt += r[0].transcript;
        askInput.value = base + txt;
        askInput.dispatchEvent(new Event("input"));
      };
      rec.onerror = (e) => { if (e.error !== "aborted" && e.error !== "no-speech") toast(t("mic_error"), "err"); };
      rec.onend = () => { rec = null; micBtn.classList.remove("rec"); askInput.focus(); };
      try {
        rec.start();
        micBtn.classList.add("rec");
      } catch { rec = null; toast(t("mic_error"), "err"); }
    });
  }

  // Regio-selectie als "bijlage" bij je vraag: eerst slepen, dan (optioneel)
  // typen, dan versturen — zoals een afbeelding meesturen in een chat-app.
  let pendingRegion = null;
  const attachPreview = el("div", { class: "attach-preview" });
  const attachLabel = el("div", { class: "attach-label" });
  const attachBar = el("div", { class: "attach-bar", style: "display:none" },
    attachPreview, attachLabel,
    el("button", { class: "attach-x", title: t("cancel"), onclick: () => clearAttachment() }, icon("x", "sm")),
  );

  function setAttachment(box) {
    pendingRegion = box;
    // De uitsnede tonen zonder canvas: de dia als background, ingezoomd op de regio.
    api.slideImageObjectUrl(hash, ctx.page).then((url) => {
      if (!attachPreview.isConnected) { URL.revokeObjectURL(url); return; }
      if (attachPreviewUrl) URL.revokeObjectURL(attachPreviewUrl);
      attachPreviewUrl = url;
      attachPreview.style.backgroundImage = `url("${url}")`;
    }).catch(() => { attachPreview.style.backgroundImage = ""; });
    attachPreview.style.backgroundSize = `${100 / box.width}% ${100 / box.height}%`;
    const px = box.width >= 1 ? 0 : (box.x / (1 - box.width)) * 100;
    const py = box.height >= 1 ? 0 : (box.y / (1 - box.height)) * 100;
    attachPreview.style.backgroundPosition = `${px}% ${py}%`;
    attachLabel.replaceChildren(
      el("strong", {}, t("region_attached", { n: ctx.page + 1 })),
      el("span", {}, t("region_attached_hint")),
    );
    attachBar.style.display = "";
    askInput.focus();
  }
  function clearAttachment() {
    pendingRegion = null;
    attachBar.style.display = "none";
  }

  // Pijltje om vanuit een lange chat terug te springen naar de uitleg bovenaan.
  const toTopBtn = el("button", { class: "to-top", title: t("back_to_top"),
    onclick: () => panelBody.scrollTo({ top: 0, behavior: "smooth" }) }, icon("up", "sm"));
  panelBody.addEventListener("scroll", () => {
    toTopBtn.classList.toggle("show", panelBody.scrollTop > 400);
  });

  const panel = el("div", { class: "panel" },
    el("div", { class: "panel-head" },
      el("div", { class: "row explain-toolbar" }, modeSeg, audSeg, el("span", { class: "spacer" }), noteBtn, refreshBtn),
    ),
    panelBody,
    toTopBtn,
    el("div", { class: "panel-foot" },
      attachBar,
      el("div", { class: "ask-row" }, askInput, micBtn, sendBtn),
    ),
  );

  // Sleepbare verdeler tussen dia en uitleg (dubbelklik = herstellen).
  const divider = el("div", { class: "panel-divider tip", "data-tip": t("tip_divider") });
  function applyPanelWidth(px) {
    panel.style.flex = `0 0 ${px}px`;
    panel.style.maxWidth = "none";
  }
  let dividerResizeFrame = 0;
  function fitSlideAfterPanelResize() {
    cancelAnimationFrame(dividerResizeFrame);
    dividerResizeFrame = requestAnimationFrame(() => {
      dividerResizeFrame = 0;
      remeasureSlideZoom();
    });
  }
  if (prefs.panelWidth > 0) applyPanelWidth(prefs.panelWidth);
  divider.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { divider.setPointerCapture(e.pointerId); } catch { /* synthetische events */ }
    divider.classList.add("dragging");
    const rect = main.getBoundingClientRect();
    const onMove = (ev) => {
      const w = Math.min(Math.max(rect.right - ev.clientX, 320), rect.width * 0.65);
      applyPanelWidth(w);
      fitSlideAfterPanelResize();
    };
    const onUp = () => {
      divider.classList.remove("dragging");
      divider.removeEventListener("pointermove", onMove);
      divider.removeEventListener("pointerup", onUp);
      fitSlideAfterPanelResize();
      savePrefs({ panelWidth: Math.round(panel.getBoundingClientRect().width) });
    };
    divider.addEventListener("pointermove", onMove);
    divider.addEventListener("pointerup", onUp);
  });
  divider.addEventListener("dblclick", () => {
    panel.style.flex = "";
    panel.style.maxWidth = "";
    fitSlideAfterPanelResize();
    savePrefs({ panelWidth: 0 });
  });

  main.append(stage, divider, panel);

  /* ---------- markeringen & notities ---------- */
  function toggleFlag(flag) {
    study.toggleFlag(hash, ctx.page, flag);
    refreshFlagChips();
    refreshNoteButton();
  }
  function refreshFlagChips() {
    const p = study.getPage(hash, ctx.page);
    starChip.classList.toggle("on", !!p.star);
    unclearChip.classList.toggle("on", !!p.unclear);
  }
  function refreshNoteButton() {
    const p = study.getPage(hash, ctx.page);
    noteBtn.classList.toggle("has-dot", !!(p.note || p.star || p.unclear));
  }

  /* ---------- raster-overzicht ---------- */
  overviewBtn.addEventListener("click", () => {
    let close;
    const grid = el("div", { class: "grid-ov" },
      ...Array.from({ length: doc.total_pages }, (_, i) => {
        const p = study.getPage(hash, i);
        const thumb = el("img", { loading: "lazy", alt: "" });
        api.setSlideImage(thumb, hash, i).catch(() => {});
        return el("button", { class: i === ctx.page ? "on" : "", onclick: () => { close(); gotoPage(i); } },
          thumb,
          el("span", { class: "n" }, i + 1),
          p.star ? el("span", { class: "fdot star show" }) : null,
          study.isWeak(hash, i) ? el("span", { class: "fdot weak show" }) : null,
        );
      }),
    );
    close = openModal(el("div", { class: "grid-wrap" },
      el("div", { class: "search-head" },
        icon("grid"),
        el("span", { style: "flex:1;font-weight:600" }, t("all_slides", { name: doc.file_name })),
        el("span", { class: "chip amber", title: t("legend_star_tip") }, t("legend_star")),
        el("span", { class: "chip red", title: t("legend_weak_tip") }, t("legend_weak")),
      ),
      el("div", { class: "grid-scroll" }, grid),
    ));
  });

  /* ---------- dia-navigatie ---------- */
  const saveProgress = debounce((p) => api.saveProgress(hash, p).catch(() => {}), 600);

  function updateSlide() {
    const p = ctx.page;
    slideFrame.classList.add("loading");
    spinner.style.display = "";
    imgRetries = 0;          // nieuwe dia = schone lei voor de laadpogingen
    imgError.style.display = "none";
    loadSlideImage();
    pageLabel.textContent = t("slide_label", { a: p + 1, b: doc.total_pages });
    prevBtn.disabled = p === 0;
    nextBtn.disabled = p === doc.total_pages - 1;
    progFill.style.width = `${((p + 1) / doc.total_pages) * 100}%`;
    refreshFlagChips();
    refreshNoteButton();
    noteLabel.textContent = t("note_label", { n: p + 1 });
    noteArea.value = study.getNote(hash, p);
    history.replaceState(null, "", `#/doc/${hash}/study/${p}`);
    saveProgress(p);
    // De volgende dia alvast in de browsercache laden: doorklikken toont de
    // afbeelding dan instant (de URL is immutable-gecachet).
    if (p + 1 < doc.total_pages) {
      const preload = new Image();
      api.setSlideImage(preload, hash, p + 1).catch(() => {});
    }
  }
  // Vlak na een upload wordt de afbeelding op de achtergrond nog gerenderd, dus
  // een mislukte laadpoging is meestal tijdelijk: opnieuw proberen met oplopende
  // pauze. Maar begrensd — eerder werd elke 1,5s eindeloos opnieuw geprobeerd,
  // waardoor een dia die écht niet laadt voor altijd bleef "laden" én de backend
  // onbeperkt bestookt werd. Na de laatste poging: gewoon eerlijk een foutje
  // tonen met een knop om het zelf nog eens te proberen.
  const MAX_IMG_RETRIES = 5;   // 1,2 + 2,4 + 4,8 + 8 + 8 s ≈ 25 s voordat we het opgeven
  let imgRetries = 0;
  const imgError = el("div", { class: "slide-error", style: "display:none" },
    icon("alert", "sm"), el("span", {}, t("slide_img_failed")),
    el("button", { class: "btn", onclick: () => { imgRetries = 0; loadSlideImage(); } },
      icon("refresh", "sm"), t("retry")),
  );
  slideFrame.append(imgError);

  function loadSlideImage() {
    imgError.style.display = "none";
    slideFrame.classList.add("loading");
    spinner.style.display = "";
    api.setSlideImage(slideImg, hash, ctx.page, "display", { cacheBust: imgRetries > 0 })
      .catch(() => slideImg.dispatchEvent(new Event("error")));
  }

  // ---- inzoomen op de dia zelf (Ctrl/Cmd + scroll boven de dia) ----
  // Nodig voor dia's met kleine grafieken of voetnoten. Bij schaal 1 laten we de
  // normale CSS het werk doen; pas bij inzoomen zetten we een expliciete breedte
  // en mag de houder scrollen, zodat je over de dia kunt schuiven. De "past
  // precies"-breedte meten we telkens opnieuw, want die hangt af van de dia en
  // de vensterbreedte. Regio-selectie blijft kloppen: die meet de afbeelding zelf.
  const SLIDE_MIN = 1, SLIDE_MAX = 4;
  let slideFitW = 0;

  function applySlideZoom() {
    const s = prefs.slideScale || 1;
    const zoomed = s > 1.001 && slideFitW > 0;
    slideHolder.classList.toggle("zoomed", zoomed);
    if (!zoomed) {
      slideImg.style.width = slideImg.style.maxWidth = slideImg.style.maxHeight = "";
      slideFrame.style.maxWidth = slideFrame.style.maxHeight = "";
      return;
    }
    slideImg.style.maxWidth = slideImg.style.maxHeight = "none";
    slideFrame.style.maxWidth = slideFrame.style.maxHeight = "none";
    slideImg.style.width = `${Math.round(slideFitW * s)}px`;
  }

  function remeasureSlideZoom() {
    // even zonder zoom meten wat de dia normaal gesproken inneemt
    slideImg.style.width = slideImg.style.maxWidth = slideImg.style.maxHeight = "";
    slideFrame.style.maxWidth = slideFrame.style.maxHeight = "";
    slideFitW = slideImg.getBoundingClientRect().width;
    applySlideZoom();
  }

  // Zoomen rond de cursor: het punt waar je op wijst blijft onder je muis staan.
  // Zonder dit zoomt hij altijd vanuit het midden en schiet het detail waar je
  // naar kijkt juist uit beeld — dat is wat "niet smooth" het meest veroorzaakt.
  slideHolder.addEventListener("wheel", (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const prev = prefs.slideScale || 1;
    // Staat de zoom op 1, dan ís de huidige breedte per definitie de "past
    // precies"-breedte. Die hier pakken i.p.v. alleen bij laden/resize, want de
    // dia wordt óók smaller of breder als je de scheiding met de uitleg versleept
    // — met een verouderde meting sprong de eerste zoomstap zichtbaar.
    if (prev === 1) slideFitW = slideImg.getBoundingClientRect().width;

    const next = clamp(prev * wheelZoomFactor(e, SLIDE_PER_NOTCH), SLIDE_MIN, SLIDE_MAX);
    if (Math.abs(next - prev) < 0.0005) return;

    // Waar wijst de cursor nu op de dia (0..1)?
    const before = slideImg.getBoundingClientRect();
    const relX = (e.clientX - before.left) / before.width;
    const relY = (e.clientY - before.top) / before.height;

    savePrefs({ slideScale: Math.round(next * 1000) / 1000 });
    applySlideZoom();

    // Schuif zo bij dat datzelfde punt weer onder de cursor ligt.
    const after = slideImg.getBoundingClientRect();
    slideHolder.scrollLeft += (after.left + relX * after.width) - e.clientX;
    slideHolder.scrollTop += (after.top + relY * after.height) - e.clientY;
  }, { passive: false });

  // Een ingezoomde dia werkt als een kaart: pak hem vast en sleep om naar een
  // ander deel te gaan. Dit is sneller en natuurlijker dan de scrollbalken.
  // In de selectiemodus blijft slepen gereserveerd voor "selecteer & vraag".
  let slidePan = null;
  slideHolder.addEventListener("pointerdown", (e) => {
    if (!slideHolder.classList.contains("zoomed") || selecting || e.button !== 0) return;
    e.preventDefault();
    try { slideHolder.setPointerCapture(e.pointerId); } catch { /* synthetische events */ }
    slidePan = {
      pointerId: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      left: slideHolder.scrollLeft,
      top: slideHolder.scrollTop,
    };
    slideHolder.classList.add("panning");
  });
  slideHolder.addEventListener("pointermove", (e) => {
    if (!slidePan || e.pointerId !== slidePan.pointerId) return;
    slideHolder.scrollLeft = slidePan.left - (e.clientX - slidePan.x);
    slideHolder.scrollTop = slidePan.top - (e.clientY - slidePan.y);
  });
  const stopSlidePan = (e) => {
    if (!slidePan || (e?.pointerId != null && e.pointerId !== slidePan.pointerId)) return;
    slidePan = null;
    slideHolder.classList.remove("panning");
  };
  slideHolder.addEventListener("pointerup", stopSlidePan);
  slideHolder.addEventListener("pointercancel", stopSlidePan);
  window.addEventListener("resize", debounce(remeasureSlideZoom, 200));

  slideImg.addEventListener("load", () => {
    imgRetries = 0;
    slideFrame.classList.remove("loading");
    spinner.style.display = "none";
    imgError.style.display = "none";
    remeasureSlideZoom();
  });
  slideImg.addEventListener("error", () => {
    if (imgRetries >= MAX_IMG_RETRIES) {
      slideFrame.classList.remove("loading");
      spinner.style.display = "none";
      imgError.style.display = "";
      return;
    }
    const wait = Math.min(1200 * 2 ** imgRetries, 8000);
    imgRetries++;
    setTimeout(() => { if (slideImg.isConnected) loadSlideImage(); }, wait);
  });

  function gotoPage(p) {
    if (p < 0 || p >= doc.total_pages || p === ctx.page) return;
    ctx.page = p;
    setSelecting(false);
    clearAttachment(); // selectie hoort bij de vorige dia
    stopSpeech();
    updateSlide();
    loadExplanation();
  }
  prevBtn.addEventListener("click", () => gotoPage(ctx.page - 1));
  nextBtn.addEventListener("click", () => gotoPage(ctx.page + 1));
  // Hover/focus = intentie om te navigeren → warm die dia alvast, dan is de klik instant.
  onIntent(prevBtn, () => { warmVariant({ page: ctx.page - 1 }); warmSpeechForPage(ctx.page - 1); });
  onIntent(nextBtn, () => { warmVariant({ page: ctx.page + 1 }); warmSpeechForPage(ctx.page + 1); });

  const onKey = (e) => {
    // stage verdwijnt bij tab-wissel of navigatie → handler opruimen
    if (!stage.isConnected) { document.removeEventListener("keydown", onKey); return; }
    const inField = /^(input|textarea|select)$/i.test(document.activeElement?.tagName || "");
    if (e.key === "Escape") { setSelecting(false); clearAttachment(); return; }
    if (inField) return;
    if (e.key === "ArrowLeft") gotoPage(ctx.page - 1);
    else if (e.key === "ArrowRight") gotoPage(ctx.page + 1);
    // Links/rechts bladert door de dia's, omhoog/omlaag leest de uitleg door.
    // Scrolt het paneel zelf niet (smal scherm: dan scrolt de werkruimte),
    // dan laten we de browser gewoon zijn gang gaan.
    else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      const step = (e.key === "ArrowDown" ? 1 : -1) * Math.round(panelBody.clientHeight * 0.25);
      if (panelBody.scrollHeight > panelBody.clientHeight + 1) {
        e.preventDefault();
        panelBody.scrollBy({ top: step, behavior: "smooth" });
      }
    }
    else if (e.key === " ") { e.preventDefault(); gotoPage(ctx.page + 1); }
    else if (e.key.toLowerCase() === "f") toggleFocusMode();
  };
  document.addEventListener("keydown", onKey);

  /* ---------- uitleg laden (streaming) ---------- */
  function streamStatus(text) {
    return el("div", { class: "stream-status" },
      el("span", { class: "dots" }, el("i"), el("i"), el("i")), text);
  }

  function answerMeta(ev, mdText, mdEl) {
    let listenBtn = null;
    if (ttsSupported()) {
      listenBtn = el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px" }, icon("volume", "sm"), t("read_aloud"));
      let playing = false;
      const reset = () => { playing = false; listenBtn.replaceChildren(icon("volume", "sm"), t("read_aloud")); };
      // De neurale stem wordt server-side gegenereerd; dat is de wachttijd bij de
      // eerste klik. Warm 'm alvast zodra de gebruiker intentie toont (hover/focus),
      // zodat de klik zelf een cache-hit is en het voorlezen vrijwel meteen start.
      let prewarmed = false;
      onIntent(listenBtn, () => { if (!prewarmed) { prewarmed = true; prewarmSpeech(mdText, prefs.language); } });
      listenBtn.addEventListener("click", () => {
        if (playing) { stopSpeech(); reset(); return; }
        playing = true;
        // eerst een laadstatus: de neurale stem moet even gegenereerd worden
        listenBtn.replaceChildren(el("span", { class: "spinner", style: "width:12px;height:12px;border-width:2px" }), t("tts_loading"));
        // mdEl = de gerenderde uitleg: daarin volgt de meeleesindicator de audio.
        speak(mdText, prefs.language, reset,
          () => { usedTTS = true; if (playing) listenBtn.replaceChildren(icon("x", "sm"), t("stop")); }, mdEl);
      });
    }
    return el("div", { class: "answer-meta" },
      // Bewust geen modelnaam of cache-status tonen: implementatiedetails —
      // de uitleg hoort overal even goed (en even snel mogelijk) te zijn.
      el("span", { class: "spacer" }),
      listenBtn,
      el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => {
        navigator.clipboard.writeText(mdText).then(() => toast(t("copied"), "ok"));
      } }, icon("copy", "sm"), t("copy")),
    );
  }

  // Snelle acties: veelgestelde vervolgvragen als één klik.
  const QUICK_ACTIONS = [
    ["sparkle", t("qa_simpler"), t("qa_simpler_p")],
    ["zap", t("qa_mnemonic"), t("qa_mnemonic_p")],
    ["doc", t("qa_example"), t("qa_example_p")],
    ["quiz", t("qa_quizme"), t("qa_quizme_p")],
  ];
  function quickChips() {
    return el("div", { class: "quick-chips" },
      ...QUICK_ACTIONS.map(([ic, label, question]) =>
        el("button", { onclick: () => submitQuestion(question, label) }, icon(ic, "sm"), label)),
    );
  }

  // Warm één specifieke uitleg-variant (dia + modus + niveau + detail) alvast op de
  // achtergrond, zodat de klik erna een cache-hit is en instant laadt. Intent-
  // gebaseerd: we roepen dit aan bij hover/focus van een knop of vlak na het laden
  // van een dia — nooit speculatief voor alles. Dubbel werk wordt tweevoudig
  // afgevangen: hier via de lokale cache + een reeds-gewarmd-set, en in de backend
  // via zijn eigen cache. Geen kwaliteitsverlies: exact dezelfde generatie, alleen
  // eerder. Buiten bereik of al (lokaal) beschikbaar? Dan doet dit niets.
  const warmedKeys = new Set();
  function warmVariant({ page = ctx.page, mode = ctx.mode,
                         audienceLevel = prefs.audienceLevel, detailLevel = prefs.detailLevel } = {}) {
    if (page < 0 || page >= doc.total_pages) return;
    const key = explainKey(hash, page, mode, audienceLevel, detailLevel, prefs.language);
    if (warmedKeys.has(key) || getCachedExplain(key)) return;
    warmedKeys.add(key);
    api.prefetchExplain(hash, page, { mode, audienceLevel, detailLevel, language: prefs.language });
  }

  // Voorleesaudio van een ándere dia voorwarmen. Dat kan alleen met de markdown
  // van díe dia, want de spraaktekst wordt in de browser afgeleid (formules →
  // "(formule)", punt achter koppen); zou de server die tekst zelf afleiden, dan
  // week hij onvermijdelijk af en klopte de cache-sleutel niet meer.
  // Daarom: polsen of de uitleg al gecachet is (kost niets, genereert niets) en
  // alleen bij een treffer de audio warmen. Nog niet klaar? Dan gewoon niet —
  // de backend is 'm waarschijnlijk nog aan het maken.
  const warmedSpeech = new Set();
  function warmSpeechForPage(page) {
    if (!usedTTS || page < 0 || page >= doc.total_pages) return;
    const key = explainKey(hash, page, ctx.mode, prefs.audienceLevel, prefs.detailLevel, prefs.language);
    if (warmedSpeech.has(key)) return;
    warmedSpeech.add(key);
    api.explainCached({
      file_hash: hash, page_index: page, language: prefs.language,
      detail_level: prefs.detailLevel, mode: ctx.mode, audience_level: prefs.audienceLevel,
    }).then((markdown) => {
      if (markdown) prewarmSpeech(markdown, prefs.language);
      else warmedSpeech.delete(key);   // nog niet klaar: later nog eens proberen
    });
  }

  // Warm vast wat de gebruiker straks waarschijnlijk gebruikt (na eerste gebruik).
  function maybePrewarm(text) {
    if (usedKeypoints && ctx.mode === "explain") warmVariant({ mode: "study" });
    // De vólgende dia warmt de backend al bij /explain; warm hier ook de VÓRIGE,
    // zodat terugbladeren (ook met de pijltjestoetsen) net zo instant is. Kost bijna
    // niets: bij normaal doorbladeren zit de vorige dia al in de cache en slaat de
    // guard 'm over — alleen ná een sprong naar een losse dia wordt hij gewarmd.
    warmVariant({ page: ctx.page - 1 });
    if (usedTTS && text) prewarmSpeech(text, prefs.language);
    // En de vólgende dia: die uitleg wordt op dit moment nog gegenereerd, dus
    // even wachten met polsen. Mist het toch, dan haalt de hover op "Volgende"
    // het alsnog op.
    if (usedTTS) {
      const target = ctx.page + 1;
      setTimeout(() => { if (ctx.page === target - 1) warmSpeechForPage(target); }, 6000);
    }
  }

  async function loadExplanation(forceRefresh = false) {
    activeAbort?.();
    stopSpeech();
    chatBox.replaceChildren();  // chat hoort bij één dia+modus-combinatie

    const key = explainKey(hash, ctx.page, ctx.mode, prefs.audienceLevel, prefs.detailLevel, prefs.language);
    const cached = !forceRefresh && getCachedExplain(key);
    if (cached) {
      let verified = cached;
      // De browsercache maakt terugbladeren direct, maar mag het
      // accountgebonden creditsysteem niet omzeilen. Online bevestigt de
      // backend daarom eerst de unlock (een snelle gedeelde cache-hit); offline
      // blijft eerder bekeken inhoud wel gewoon leesbaar.
      if (navigator.onLine) {
        const status = streamStatus(t("ai_looking"));
        explainBox.replaceChildren(status);
        try {
          const data = await api.explain({
            file_hash: hash, page_index: ctx.page, language: prefs.language,
            detail_level: prefs.detailLevel, mode: ctx.mode,
            audience_level: prefs.audienceLevel, force_refresh: false,
          });
          verified = data?.markdown || cached;
          setCachedExplain(key, verified);
          const activeKey = explainKey(hash, ctx.page, ctx.mode, prefs.audienceLevel, prefs.detailLevel, prefs.language);
          if (activeKey !== key) return;
        } catch (err) {
          explainBox.replaceChildren(errorBox(err, () => loadExplanation(false)));
          return;
        }
      }
      const box = el("div", { class: "md", html: renderMarkdown(verified) });
      explainBox.replaceChildren(box);
      renderCharts(box);
      explainBox.append(answerMeta({ cached: true }, verified, box), quickChips());
      renderChatHistory();
      maybePrewarm(verified);
      return;
    }

    const mdContainer = el("div", { class: "md" });
    const status = streamStatus(t("ai_looking"));
    explainBox.replaceChildren(status,
      el("div", { class: "skeleton" }, ...[92, 100, 96, 62, 0, 88, 94, 70].map(w =>
        w ? el("div", { class: "bone", style: `width:${w}%` }) : el("div", { style: "height:6px" }))),
    );

    const renderer = createStreamRenderer(mdContainer);
    let started = false;
    const requestPage = ctx.page;

    activeAbort = api.explainStream({
      file_hash: hash,
      page_index: requestPage,
      language: prefs.language,
      detail_level: prefs.detailLevel,
      mode: ctx.mode,
      audience_level: prefs.audienceLevel,
      force_refresh: forceRefresh,
    }, {
      onStart(ev) {
        // Deze dia wordt al gegenereerd (meestal door de prefetch). Zeg dat,
        // in plaats van dezelfde spinner als bij gewoon laden te tonen.
        if (ev?.status === "waiting") {
          status.replaceChildren(el("span", { class: "dots" }, el("i"), el("i"), el("i")), t("ai_waiting"));
        }
      },
      onDelta(text) {
        if (!started) { started = true; explainBox.replaceChildren(mdContainer); }
        renderer.append(text);
      },
      onDone(ev) {
        renderer.finish();
        if (!started) explainBox.replaceChildren(mdContainer);
        setCachedExplain(key, renderer.text);
        explainBox.append(answerMeta(ev, renderer.text, mdContainer), quickChips());
        renderChatHistory();
        maybePrewarm(renderer.text);
      },
      onError(err) {
        explainBox.replaceChildren(errorBox(err, () => loadExplanation(forceRefresh)));
      },
    });
  }

  function errorBox(err, retry) {
    return el("div", { class: "md-error" }, icon("alert"),
      el("div", {},
        el("div", { style: "font-weight:600;margin-bottom:2px" }, t("gen_failed")),
        el("div", { style: "color:var(--text-soft)" }, err.message),
        el("button", { class: "btn", style: "margin-top:10px", onclick: retry }, icon("refresh", "sm"), t("retry")),
      ));
  }

  /* ---------- vervolgvragen (chat) ---------- */
  // Kom je terug op een dia waar je eerder vragen stelde, dan zijn die eerder
  // volledig uitgeklapt meegekomen en duwden ze de uitleg helemaal weg. Nu staan
  // ze ingeklapt als knopje met de vraag; klikken toont het bewaarde antwoord
  // (uit de cache, dus zonder nieuwe AI-aanroep). Een vraag die je nét stelt
  // blijft wél gewoon openstaan — die wil je meteen lezen.
  function renderChatHistory() {
    chatBox.replaceChildren();
    for (const turn of getChat(hash, ctx.page)) {
      chatBox.append(collapsedChatTurn(turn));
    }
  }

  function collapsedChatTurn(turn) {
    const question = turn.display || turn.question || (turn.region ? t("region_default_q") : "");
    const body = el("div", { class: "chat-a", style: "display:none" });
    let filled = false;
    const chevron = icon("right", "sm");
    const btn = el("button", { class: "chat-recall", "aria-expanded": "false" },
      turn.region ? icon("target", "sm") : icon("quiz", "sm"),
      el("span", { class: "q" }, question),
      chevron,
    );
    btn.addEventListener("click", () => {
      const open = body.style.display !== "none";
      if (!open && !filled) {
        filled = true;                       // pas renderen als je het opent
        const aBody = el("div", { class: "md", html: renderMarkdown(turn.answer) });
        renderCharts(aBody);
        body.append(aBody);
      }
      body.style.display = open ? "none" : "";
      btn.classList.toggle("open", !open);
      btn.setAttribute("aria-expanded", String(!open));
    });
    return el("div", { class: "chat-turn collapsed" }, btn, body);
  }

  function appendChatTurn(question, answerMd, isRegion, scroll = true) {
    const aBody = el("div", { class: "md", html: renderMarkdown(answerMd) });
    renderCharts(aBody);
    const node = el("div", { class: "chat-turn" },
      el("div", { class: "chat-q" },
        isRegion ? el("span", { class: "chip accent" }, icon("target", "sm"), t("region_chip")) : null,
        question || (isRegion ? t("region_default_q") : "")),
      el("div", { class: "chat-a" }, aBody),
    );
    chatBox.append(node);
    if (scroll) scrollChatTo(node);
    return aBody;
  }

  // q/display optioneel: zonder argumenten wordt het invoerveld gebruikt,
  // met argumenten is het een snelle actie (chip).
  function submitQuestion(presetQuestion = null, presetLabel = null) {
    const q = presetQuestion || askInput.value.trim();
    if (chatAbort) return;

    // Met een regio-bijlage gaat de vraag (of de standaardvraag) over die regio.
    if (!presetQuestion && pendingRegion) {
      const box = pendingRegion;
      clearAttachment();
      askInput.value = "";
      askInput.style.height = "auto";
      askRegion(box, q);
      return;
    }

    if (!q) return;
    if (!presetQuestion) {
      askInput.value = "";
      askInput.style.height = "auto";
    }
    sendBtn.disabled = true;

    const page = ctx.page;
    const explanation = getCachedExplain(explainKey(hash, page, ctx.mode, prefs.audienceLevel, prefs.detailLevel, prefs.language)) || "";
    const history = [];
    if (explanation) history.push({ role: "assistant", content: explanation });
    for (const turn of getChat(hash, page)) {
      history.push({ role: "user", content: turn.question });
      history.push({ role: "assistant", content: turn.answer });
    }

    const aBody = appendChatTurn(presetLabel || q, "", false);
    const status = streamStatus(t("thinking"));
    aBody.before(status);
    const renderer = createStreamRenderer(aBody);

    chatAbort = api.explainStream({
      file_hash: hash,
      page_index: page,
      language: prefs.language,
      detail_level: prefs.detailLevel,
      mode: ctx.mode,
      audience_level: prefs.audienceLevel,
      question: q,
      history,
    }, {
      onDelta(text) { status.remove(); renderer.append(text); scrollChatBottom(); },
      onDone() {
        status.remove();
        renderer.finish();
        chatAbort = null;
        sendBtn.disabled = false;
        getChat(hash, page).push({ question: q, display: presetLabel || q, answer: renderer.text, region: false });
      },
      onError(err) {
        status.remove();
        aBody.innerHTML = "";
        aBody.append(errorBox(err, () => { aBody.closest(".chat-turn")?.remove(); submitQuestion(q, presetLabel); }));
        chatAbort = null;
        sendBtn.disabled = false;
      },
    });
  }

  /* ---------- selecteer & vraag (regio) ---------- */
  // Robuust tegen snel klikken: één hint-element, één drag tegelijk, en
  // pointer capture zodat de muis buiten de dia mag eindigen.
  let selecting = false;
  let drag = null; // { rect, x0, y0, box }
  const hintEl = el("div", { class: "region-hint" }, t("region_hint"));

  function setSelecting(on) {
    selecting = on;
    slideFrame.classList.toggle("selecting", on);
    regionBtn.classList.toggle("on", on);
    if (on) {
      if (!hintEl.isConnected) slideHolder.append(hintEl);
    } else {
      hintEl.remove();
      drag?.box.remove();
      drag = null;
    }
  }
  regionBtn.addEventListener("click", () => setSelecting(!selecting));

  slideFrame.addEventListener("pointerdown", (e) => {
    if (!selecting || drag || e.button !== 0) return;
    e.preventDefault();
    try { slideFrame.setPointerCapture(e.pointerId); } catch { /* synthetische events */ }
    const box = el("div", { class: "region-box" });
    slideFrame.append(box);
    drag = { rect: slideImg.getBoundingClientRect(), x0: e.clientX, y0: e.clientY, box };
  });
  slideFrame.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const { rect, x0, y0, box } = drag;
    const x = Math.max(rect.left, Math.min(e.clientX, rect.right));
    const y = Math.max(rect.top, Math.min(e.clientY, rect.bottom));
    box.style.left = `${Math.min(x0, x) - rect.left}px`;
    box.style.top = `${Math.min(y0, y) - rect.top}px`;
    box.style.width = `${Math.abs(x - x0)}px`;
    box.style.height = `${Math.abs(y - y0)}px`;
  });
  const endDrag = (e) => {
    if (!drag) return;
    const { rect, x0, y0 } = drag;
    const x1 = Math.max(rect.left, Math.min(e.clientX, rect.right));
    const y1 = Math.max(rect.top, Math.min(e.clientY, rect.bottom));
    const norm = {
      x: (Math.min(x0, x1) - rect.left) / rect.width,
      y: (Math.min(y0, y1) - rect.top) / rect.height,
      width: Math.abs(x1 - x0) / rect.width,
      height: Math.abs(y1 - y0) / rect.height,
    };
    setSelecting(false);
    // Niet direct vragen: de selectie wordt een bijlage bij het invoerveld,
    // zodat je er nog een gerichte vraag bij kunt typen.
    if (norm.width >= 0.02 && norm.height >= 0.02) setAttachment(norm);
  };
  slideFrame.addEventListener("pointerup", endDrag);
  slideFrame.addEventListener("pointercancel", () => setSelecting(false));

  function askRegion(box, question = "") {
    const page = ctx.page;
    sendBtn.disabled = true;

    const aBody = appendChatTurn(question, "", true);
    const status = streamStatus(t("ai_region"));
    aBody.before(status);
    const renderer = createStreamRenderer(aBody);

    chatAbort?.();
    chatAbort = api.askRegionStream({
      file_hash: hash,
      page_index: page,
      box,
      question,
      language: prefs.language,
    }, {
      onDelta(text) { status.remove(); renderer.append(text); scrollChatBottom(); },
      onDone() {
        status.remove();
        renderer.finish();
        chatAbort = null;
        sendBtn.disabled = false;
        getChat(hash, page).push({ question: question || t("region_default_q"), answer: renderer.text, region: true });
      },
      onError(err) {
        status.remove();
        aBody.append(errorBox(err, () => { aBody.closest(".chat-turn")?.remove(); askRegion(box, question); }));
        chatAbort = null;
        sendBtn.disabled = false;
      },
    });
  }

  /* ---------- start ---------- */
  updateSlide();
  loadExplanation();
}
