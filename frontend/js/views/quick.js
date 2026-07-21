// Snel-foto: maak een foto van een opgave → directe stap-voor-stap uitleg →
// vervolgvraag → volgende foto. Bewust een minimaal, los scherm (geen tabs,
// geen dia-navigatie), voor "even snel iets snappen" i.p.v. een heel college
// bestuderen. De foto wordt als kind="quick" geüpload zodat hij de gewone
// documentenlijst niet vervuilt.
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { createStreamRenderer } from "../markdown.js";
import { openModal } from "../util.js";
import { navigate } from "../app.js";

const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf";

export function renderQuick(root, hash) {
  if (hash) renderExplain(root, hash);
  else renderCapture(root);
}

/* ---------- opnamescherm ---------- */
function renderCapture(root) {
  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("zap")), ""),
    el("div", { class: "doc-name" }, t("quick_title")),
    el("div", { class: "spacer" }),
  );

  // capture="environment" opent op mobiel meteen de achtercamera.
  const camInput = el("input", { type: "file", accept: "image/*", capture: "environment", style: "display:none" });
  const fileInput = el("input", { type: "file", accept: ACCEPT, style: "display:none" });
  camInput.addEventListener("change", () => { if (camInput.files[0]) startUpload(camInput.files[0]); });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) startUpload(fileInput.files[0]); });

  const dropzone = el("div", { class: "dropzone", role: "button", tabindex: "0" },
    el("div", { class: "dz-icon" }, icon("zap", "lg")),
    el("h3", {}, t("quick_drop_title")),
    el("p", {}, t("quick_drop_sub")),
  );
  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => { e.preventDefault(); dropzone.classList.remove("drag"); if (e.dataTransfer.files?.[0]) startUpload(e.dataTransfer.files[0]); });

  const area = el("div", {}, dropzone);

  async function startUpload(file) {
    const fill = el("div", { class: "progress-fill", style: "width:0%" });
    const status = el("p", { style: "margin:10px 0 0;font-size:12.5px;color:var(--muted)" }, t("uploading"));
    area.replaceChildren(el("div", { class: "upload-progress" },
      el("div", { class: "row" }, el("span", { class: "name" }, file.name), el("span", { class: "pct" })),
      el("div", { class: "progress-track" }, fill), status));
    try {
      const doc = await api.upload(file, (frac) => {
        fill.style.width = `${Math.round(frac * 100)}%`;
        if (frac >= 1) { status.textContent = t("processing"); fill.classList.add("indeterminate"); }
      }, "quick");
      const go = () => navigate(`#/quick/${doc.file_hash}`);
      // Alleen bij een foto die de backend als wazig/donker markeert: even
      // vragen of ze doorgaan of een scherpere foto maken. Nooit blokkerend.
      if (doc.image_quality?.issues?.length) warnPhotoQuality(doc.image_quality, go);
      else go();
    } catch (err) {
      toast(err.message, "err", 5000);
      area.replaceChildren(dropzone);
    }
  }

  // Vriendelijke waarschuwing + keuze: opnieuw maken of toch doorgaan.
  function warnPhotoQuality(iq, onContinue) {
    const reasons = (iq.issues || []).map((c) =>
      c === "blurry" ? t("photo_blurry") : c === "dark" ? t("photo_dark") : c);
    let msg = reasons.join(" · ");
    msg = msg.charAt(0).toUpperCase() + msg.slice(1) + ".";
    let close;
    close = openModal(el("div", {},
      el("div", { class: "confirm-body" },
        el("h3", {}, t("photo_quality_title")),
        el("p", { style: "margin:6px 0 0" }, msg),
        el("p", { style: "margin:8px 0 0;font-size:13px;color:var(--muted)" }, t("photo_quality_hint")),
      ),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => { close(); area.replaceChildren(dropzone); } },
          icon("refresh", "sm"), t("photo_retake")),
        el("button", { class: "btn primary", onclick: () => { close(); onContinue(); } }, t("photo_continue")),
      ),
    ), { center: true, small: true });
  }

  const body = el("div", { class: "home" },
    el("div", { class: "home-inner", style: "max-width:640px" },
      el("div", { class: "hero", style: "margin-bottom:22px" },
        el("h1", { style: "font-size:clamp(24px,4vw,34px)" }, t("quick_hero")),
        el("p", {}, t("quick_hero_sub"))),
      area,
      el("div", { style: "display:flex;gap:10px;justify-content:center;margin-top:14px;flex-wrap:wrap" },
        el("button", { class: "btn primary lg", onclick: () => camInput.click() }, icon("image", "sm"), t("quick_take_photo")),
        el("button", { class: "btn lg", onclick: () => fileInput.click() }, icon("upload", "sm"), t("quick_choose_file"))),
    ),
  );

  root.append(topbar, body);
}

/* ---------- uitlegscherm ---------- */
async function renderExplain(root, hash) {
  const loading = el("div", { style: "flex:1;display:grid;place-items:center" }, el("div", { class: "spinner" }));
  root.append(loading);

  let doc;
  try { doc = await api.getDocument(hash); }
  catch (err) {
    loading.remove();
    root.append(el("div", { style: "flex:1;display:grid;place-items:center;padding:24px" },
      el("div", { class: "empty-state" }, el("p", {}, err.message),
        el("button", { class: "btn primary", style: "margin-top:10px", onclick: () => navigate("#/quick") }, t("quick_new")))));
    return;
  }
  loading.remove();

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand", style: "font-size:14px" }, el("span", { class: "logo" }, icon("zap")), ""),
    el("div", { class: "spacer" }),
    el("button", { class: "btn ghost", onclick: () => saveToFolder(hash) }, icon("folder", "sm"), t("quick_save")),
    el("button", { class: "btn primary", onclick: () => navigate("#/quick") }, icon("zap", "sm"), t("quick_next")),
  );

  // in-memory chatgeschiedenis voor deze foto
  const history = [];  // [{role, content}]
  let explanationMd = "";

  const photo = el("img", { src: api.slideImageUrl(hash, 0, "display"), alt: "", class: "quick-photo" });
  const explainBox = el("div", { class: "md" });
  const chatBox = el("div", {});

  const askInput = el("textarea", { class: "", rows: "1", placeholder: t("quick_ask_ph") });
  const sendBtn = el("button", { class: "send", title: t("send") }, icon("send"));
  askInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
  sendBtn.addEventListener("click", () => submit());

  const inner = el("div", { class: "content-inner narrow", style: "max-width:720px" },
    el("div", { class: "quick-photo-wrap" }, photo),
    explainBox, chatBox);
  const page = el("div", { class: "content-page" }, inner);
  const foot = el("div", { class: "panel-foot" },
    el("div", { class: "ask-row" }, askInput, sendBtn),
    el("div", { class: "hint" }, t("quick_hint")));

  root.append(topbar, page, foot);

  // eerste uitleg streamen
  loadExplanation();

  function loadExplanation() {
    const status = el("div", { class: "stream-status" }, el("span", { class: "dots" }, el("i"), el("i"), el("i")), t("ai_looking"));
    explainBox.replaceChildren(status);
    const mdc = el("div", { class: "md" });
    const renderer = createStreamRenderer(mdc);
    let started = false;
    api.explainStream({
      file_hash: hash, page_index: 0, language: prefs.language,
      detail_level: prefs.detailLevel, mode: "explain", audience_level: prefs.audienceLevel,
    }, {
      onDelta(text) { if (!started) { started = true; explainBox.replaceChildren(mdc); } renderer.append(text); },
      onDone() { renderer.finish(); explanationMd = renderer.text; },
      onError(err) {
        explainBox.replaceChildren(el("div", { class: "md-error" }, icon("alert"),
          el("div", {}, el("div", { style: "font-weight:600" }, t("gen_failed")),
            el("button", { class: "btn", style: "margin-top:8px", onclick: loadExplanation }, icon("refresh", "sm"), t("retry")))));
      },
    });
  }

  function submit() {
    const q = askInput.value.trim();
    if (!q) return;
    askInput.value = "";
    askInput.style.height = "auto";

    // geschiedenis: de uitleg als eerste assistant-beurt, dan eerdere Q&A
    const hist = [];
    if (explanationMd) hist.push({ role: "assistant", content: explanationMd });
    for (const turn of history) hist.push(turn);

    const aBody = el("div", { class: "md" });
    chatBox.append(el("div", { class: "chat-turn" },
      el("div", { class: "chat-q" }, q),
      el("div", { class: "chat-a" }, aBody)));
    const renderer = createStreamRenderer(aBody);

    api.explainStream({
      file_hash: hash, page_index: 0, language: prefs.language,
      detail_level: prefs.detailLevel, mode: "explain", audience_level: prefs.audienceLevel,
      question: q, history: hist,
    }, {
      onDelta(text) { renderer.append(text); },
      onDone() {
        renderer.finish();
        history.push({ role: "user", content: q });
        history.push({ role: "assistant", content: renderer.text });
      },
      onError(err) { aBody.replaceChildren(el("div", { class: "md-error" }, icon("alert"), err.message)); },
    });
  }

  // Bewaar de foto in een vak (map). Zodra hij een folder_id heeft verschijnt
  // hij in dat vak — óók al is hij als "quick" geüpload, want de mapweergave
  // filtert op folder_id, niet op kind.
  async function saveToFolder(fileHash) {
    let folders = [];
    try { folders = (await api.folders()).folders || []; } catch (err) { toast(err.message, "err"); return; }
    let close;
    const assign = async (folderId, name) => {
      try { await api.setDocumentFolder(fileHash, folderId); close(); toast(t("moved_to_folder", { name }), "ok"); }
      catch (err) { toast(err.message, "err"); }
    };
    const createNew = async () => {
      const input = el("input", { class: "field", placeholder: t("folder_name_ph"), maxlength: "80" });
      const box = el("div", {}, el("p", { style: "margin:0 0 8px;color:var(--muted);font-size:13px" }, t("new_folder")), input);
      list.replaceChildren(box, el("button", { class: "btn primary", style: "margin-top:10px", onclick: async () => {
        const name = input.value.trim(); if (!name) return;
        try { const res = await api.folderCreate(name); assign(res.folder.id, name); } catch (err) { toast(err.message, "err"); }
      } }, t("create")));
      input.focus();
    };
    const list = el("div", { class: "folder-pick" },
      ...folders.map(f => el("button", { class: "folder-pick-row", onclick: () => assign(f.id, f.name) },
        icon("folder", "sm"), el("span", { style: "flex:1;text-align:left" }, f.name))),
      el("button", { class: "folder-pick-row new", onclick: createNew },
        icon("folder-plus", "sm"), el("span", { style: "flex:1;text-align:left" }, t("new_folder"))),
    );
    close = openModal(el("div", {},
      el("div", { class: "confirm-body" }, el("h3", {}, t("quick_save")), list),
      el("div", { class: "confirm-foot" }, el("button", { class: "btn", onclick: () => close() }, t("cancel")))),
      { center: true, small: true });
  }
}
