// Werkruimte: topbar met tabs + studeer-tab (dia links, AI-uitleg rechts).
import { api } from "../api.js";
import { el, icon, toast, debounce, openModal } from "../util.js";
import { prefs, savePrefs, explainKey, getCachedExplain, setCachedExplain, getChat } from "../state.js";
import { createStreamRenderer, renderMarkdown } from "../markdown.js";
import { study } from "../stats.js";
import { speak, stopSpeech, ttsSupported } from "../tts.js";
import { t, uiLocale } from "../i18n.js";
import { openSearch } from "../search.js";
import { openSettings, navigate, setFocusMode, toggleFocusMode } from "../app.js";
import { mountSummary } from "./summary.js";
import { mountQuiz } from "./quiz.js";
import { mountFlashcards } from "./flashcards.js";
import { mountExam } from "./exam.js";

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
  const tabsDef = [
    ["study", "book", t("tab_study")],
    ["summary", "summary", t("tab_summary")],
    ["quiz", "quiz", t("tab_quiz")],
    ["exam", "cap", t("tab_exam")],
    ["cards", "cards", t("tab_cards")],
  ];
  const main = el("div", { class: "workspace" });
  const tabBtns = {};
  const cardsBadge = el("span", { class: "badge", style: "display:none" });

  const setTab = (tKey) => {
    tab = tKey;
    history.replaceState(null, "", `#/doc/${fileHash}/${tKey}${tKey === "study" ? `/${ctx.page}` : ""}`);
    for (const [key, btn] of Object.entries(tabBtns)) btn.classList.toggle("on", key === tKey);
    setFocusMode(false);
    stopSpeech();
    main.replaceChildren();
    if (tKey === "study") mountStudy(main, ctx);
    else if (tKey === "summary") mountSummary(main, ctx);
    else if (tKey === "quiz") mountQuiz(main, ctx);
    else if (tKey === "exam") mountExam(main, { scope: { file_hash: fileHash }, name: doc.file_name });
    else if (tKey === "cards") mountFlashcards(main, ctx, cardsBadge);
  };

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("book")), ""),
    el("div", { class: "doc-name", title: doc.file_name }, doc.file_name),
    el("div", { class: "spacer" }),
    el("div", { class: "tabs" },
      ...tabsDef.map(([key, ic, label]) => {
        const b = el("button", { class: key === tab ? "on" : "", onclick: () => setTab(key) },
          icon(ic, "sm"), el("span", { class: "lbl" }, label), key === "cards" ? cardsBadge : null);
        tabBtns[key] = b;
        return b;
      }),
    ),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost icon-btn", title: t("tip_search"), onclick: () => openSearch({ fileHash, onPick: (h) => { if (h.file_hash === fileHash) { ctx.page = h.page_index; setTab("study"); } else navigate(`#/doc/${h.file_hash}/study/${h.page_index}`); } }) }, icon("search")),
    el("button", { class: "btn ghost icon-btn", title: t("tip_focus"), onclick: toggleFocusMode }, icon("focus")),
    el("button", { class: "btn ghost icon-btn", title: t("settings"), onclick: () => openSettings() }, icon("settings")),
  );

  // Klein rond kruisje — bewust geen timer of tekst, dat leidt af.
  const focusExit = el("button", { class: "focus-exit", title: t("exit_focus"), onclick: () => setFocusMode(false) },
    icon("x"));

  root.append(topbar, main, focusExit);

  // flashcards-badge alvast vullen (hoeveel kaarten zijn nu 'due')
  api.flashcardsGet(fileHash).then((data) => {
    if (data?.due_count > 0) { cardsBadge.textContent = data.due_count; cardsBadge.style.display = ""; }
  }).catch(() => {});

  setTab(tab);
}

/* =====================================================================
   STUDEER-TAB
   ===================================================================== */
function mountStudy(main, ctx) {
  const { doc, hash } = ctx;
  let activeAbort = null;   // lopende uitleg-stream
  let chatAbort = null;     // lopende chat/regio-stream

  /* ---------- linkerkant: dia ---------- */
  const slideImg = el("img", { alt: `${t("slide_n", { n: ctx.page + 1 })}`, draggable: "false" });
  const slideFrame = el("div", { class: "slide-frame" }, slideImg);
  const spinner = el("div", { class: "slide-spinner", style: "display:none" }, el("div", { class: "spinner" }));
  slideFrame.append(spinner);
  const slideHolder = el("div", { class: "slide-holder" }, slideFrame);

  // -- navigatie-elementen --
  const regionBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_region") }, icon("crop", "sm"));
  const overviewBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_overview") }, icon("grid", "sm"));

  const pageLabel = el("span", { class: "page-label" });
  const prevBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_prev") }, icon("left", "sm"), el("span", { class: "lbl" }, t("prev")));
  const nextBtn = el("button", { class: "nav-btn tip", "data-tip": t("tip_next") }, el("span", { class: "lbl" }, t("next")), icon("right", "sm"));

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
    modeSeg.append(el("button", { class: val === ctx.mode ? "on" : "", onclick: (e) => {
      ctx.mode = val;
      modeSeg.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      e.currentTarget.classList.add("on");
      loadExplanation();
    } }, label));
  }

  const audSeg = el("div", { class: "seg subtle" });
  const audiences = [["beginner", t("lvl_beginner")], ["intermediate", t("lvl_mid")], ["advanced", t("lvl_adv")]];
  for (const [val, label] of audiences) {
    audSeg.append(el("button", { class: val === prefs.audienceLevel ? "on" : "", onclick: (e) => {
      savePrefs({ audienceLevel: val });
      audSeg.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      e.currentTarget.classList.add("on");
      loadExplanation();
    } }, label));
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
    micBtn = el("button", { class: "mic tip", "data-tip": t("tip_mic") }, icon("mic", "sm"));
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
    attachPreview.style.backgroundImage = `url("${api.slideImageUrl(hash, ctx.page)}")`;
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
      el("div", { class: "row" }, modeSeg, audSeg, el("span", { class: "spacer" }), noteBtn, refreshBtn),
    ),
    panelBody,
    toTopBtn,
    el("div", { class: "panel-foot" },
      attachBar,
      el("div", { class: "ask-row" }, askInput, micBtn, sendBtn),
      el("div", { class: "hint" }, t("ask_hint")),
    ),
  );

  // Sleepbare verdeler tussen dia en uitleg (dubbelklik = herstellen).
  const divider = el("div", { class: "panel-divider tip", "data-tip": t("tip_divider") });
  function applyPanelWidth(px) {
    panel.style.flex = `0 0 ${px}px`;
    panel.style.maxWidth = "none";
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
    };
    const onUp = () => {
      divider.classList.remove("dragging");
      divider.removeEventListener("pointermove", onMove);
      divider.removeEventListener("pointerup", onUp);
      savePrefs({ panelWidth: Math.round(panel.getBoundingClientRect().width) });
    };
    divider.addEventListener("pointermove", onMove);
    divider.addEventListener("pointerup", onUp);
  });
  divider.addEventListener("dblclick", () => {
    panel.style.flex = "";
    panel.style.maxWidth = "";
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
        return el("button", { class: i === ctx.page ? "on" : "", onclick: () => { close(); gotoPage(i); } },
          el("img", { src: api.slideImageUrl(hash, i), loading: "lazy", alt: "" }),
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
    slideImg.src = api.slideImageUrl(hash, p);
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
    if (p + 1 < doc.total_pages) new Image().src = api.slideImageUrl(hash, p + 1);
  }
  slideImg.addEventListener("load", () => { slideFrame.classList.remove("loading"); spinner.style.display = "none"; });
  slideImg.addEventListener("error", () => {
    // afbeelding wordt op de achtergrond nog gerenderd — even opnieuw proberen
    setTimeout(() => { if (slideImg.isConnected) slideImg.src = api.slideImageUrl(hash, ctx.page) + `&r=${Date.now()}`; }, 1500);
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

  const onKey = (e) => {
    // stage verdwijnt bij tab-wissel of navigatie → handler opruimen
    if (!stage.isConnected) { document.removeEventListener("keydown", onKey); return; }
    const inField = /^(input|textarea|select)$/i.test(document.activeElement?.tagName || "");
    if (e.key === "Escape") { setSelecting(false); clearAttachment(); return; }
    if (inField) return;
    if (e.key === "ArrowLeft") gotoPage(ctx.page - 1);
    else if (e.key === "ArrowRight") gotoPage(ctx.page + 1);
    else if (e.key === " ") { e.preventDefault(); gotoPage(ctx.page + 1); }
    else if (e.key.toLowerCase() === "f") toggleFocusMode();
  };
  document.addEventListener("keydown", onKey);

  /* ---------- uitleg laden (streaming) ---------- */
  function streamStatus(text) {
    return el("div", { class: "stream-status" },
      el("span", { class: "dots" }, el("i"), el("i"), el("i")), text);
  }

  function answerMeta(ev, mdText) {
    let listenBtn = null;
    if (ttsSupported()) {
      listenBtn = el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px" }, icon("volume", "sm"), t("read_aloud"));
      let playing = false;
      const reset = () => { playing = false; listenBtn.replaceChildren(icon("volume", "sm"), t("read_aloud")); };
      listenBtn.addEventListener("click", () => {
        if (playing) { stopSpeech(); reset(); return; }
        playing = true;
        // eerst een laadstatus: de neurale stem moet even gegenereerd worden
        listenBtn.replaceChildren(el("span", { class: "spinner", style: "width:12px;height:12px;border-width:2px" }), t("tts_loading"));
        speak(mdText, prefs.language, reset,
          () => { if (playing) listenBtn.replaceChildren(icon("x", "sm"), t("stop")); });
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

  function loadExplanation(forceRefresh = false) {
    activeAbort?.();
    stopSpeech();
    chatBox.replaceChildren();  // chat hoort bij één dia+modus-combinatie

    const key = explainKey(hash, ctx.page, ctx.mode, prefs.audienceLevel, prefs.detailLevel, prefs.language);
    const cached = !forceRefresh && getCachedExplain(key);
    if (cached) {
      explainBox.replaceChildren(el("div", { class: "md", html: renderMarkdown(cached) }));
      explainBox.append(answerMeta({ cached: true }, cached), quickChips());
      renderChatHistory();
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
      onDelta(text) {
        if (!started) { started = true; explainBox.replaceChildren(mdContainer); }
        renderer.append(text);
      },
      onDone(ev) {
        renderer.finish();
        if (!started) explainBox.replaceChildren(mdContainer);
        setCachedExplain(key, renderer.text);
        explainBox.append(answerMeta(ev, renderer.text), quickChips());
        renderChatHistory();
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
  function renderChatHistory() {
    chatBox.replaceChildren();
    for (const turn of getChat(hash, ctx.page)) {
      appendChatTurn(turn.display || turn.question, turn.answer, turn.region, false);
    }
  }

  function appendChatTurn(question, answerMd, isRegion, scroll = true) {
    const aBody = el("div", { class: "md", html: renderMarkdown(answerMd) });
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
