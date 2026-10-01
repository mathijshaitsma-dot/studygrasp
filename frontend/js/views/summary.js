// Samenvatting van het hele document (gestreamd, gecachet door de backend).
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { createStreamRenderer } from "../markdown.js";
import { downloadText } from "../export.js";

const sessionCache = new Map(); // scope|lang -> markdown

export function mountSummary(main, ctx) {
  const { hash, doc } = ctx;
  mountSummaryView(main, {
    cacheScope: hash,
    title: t("tab_summary"),
    subtitle: t("summary_sub", { name: doc.file_name }),
    fileBase: doc.file_name.replace(/\.[^.]+$/, ""),
    statusLabel: () => t("summary_status", { n: doc.total_pages }),
    start: (force, handlers) => api.summaryStream(
      { file_hash: hash, language: prefs.language, force_refresh: force }, handlers),
  });
}

// Samenvatting over een heel vak: alle colleges in de map en zijn submappen
// samen. Zelfde scherm als bij één document — alleen de bron verschilt.
export function mountFolderSummary(main, folder) {
  const total = folder.total_document_count ?? folder.document_count ?? 0;
  mountSummaryView(main, {
    cacheScope: `folder:${folder.id}`,
    title: t("folder_summary_btn"),
    subtitle: t("folder_summary_sub", { name: folder.name, n: total }),
    fileBase: folder.name,
    statusLabel: () => t("folder_summary_status", { n: total }),
    start: (force, handlers) => api.folderSummaryStream(
      { folder_id: folder.id, language: prefs.language, force_refresh: force }, handlers),
  });
}

function mountSummaryView(main, ctx) {
  let abort = null;

  const mdContainer = el("div", { class: "md" });
  const statusArea = el("div", {});
  const actions = el("div", { class: "answer-meta no-print", style: "display:none" });

  const inner = el("div", { class: "content-inner" },
    el("h1", { class: "page-title" }, icon("summary"), ctx.title),
    el("p", { class: "page-sub" }, ctx.subtitle),
    statusArea, mdContainer, actions,
  );
  const page = el("div", { class: "content-page" }, inner);
  main.append(page);

  function showActions(mdText, ev) {
    actions.style.display = "";
    actions.replaceChildren(
      el("span", { class: "spacer" }),
      el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => navigator.clipboard.writeText(mdText).then(() => toast(t("summary_copied"), "ok")) }, icon("copy", "sm"), t("copy")),
      el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => {
        downloadText(`${ctx.fileBase} - samenvatting.md`, "text/markdown", mdText);
      } }, icon("download", "sm"), t("download")),
      el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => window.print() }, icon("printer", "sm"), t("export_print")),
      el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px", onclick: () => load(true) }, icon("refresh", "sm"), t("regenerate")),
    );
  }

  function load(force = false) {
    abort?.();
    actions.style.display = "none";
    const cacheKey = `${ctx.cacheScope}|${prefs.language}`;
    const cached = !force && sessionCache.get(cacheKey);
    const renderer = createStreamRenderer(mdContainer);
    if (cached) {
      renderer.set(cached);
      renderer.finish();
      showActions(cached, { cached: true });
      return;
    }

    statusArea.replaceChildren(
      el("div", { class: "stream-status" },
        el("span", { class: "dots" }, el("i"), el("i"), el("i")),
        ctx.statusLabel()),
    );
    mdContainer.innerHTML = "";

    abort = ctx.start(force, {
      onDelta(text) { statusArea.replaceChildren(); renderer.append(text); },
      onDone(ev) {
        statusArea.replaceChildren();
        renderer.finish();
        sessionCache.set(cacheKey, renderer.text);
        showActions(renderer.text, ev);
      },
      onError(err) {
        statusArea.replaceChildren(
          el("div", { class: "md-error" }, icon("alert"),
            el("div", {},
              el("div", { style: "font-weight:600" }, t("summary_failed")),
              el("div", { style: "color:var(--text-soft)" }, err.message),
              el("button", { class: "btn", style: "margin-top:10px", onclick: () => load(force) }, icon("refresh", "sm"), t("retry")))),
        );
      },
    });
  }

  load();
}
