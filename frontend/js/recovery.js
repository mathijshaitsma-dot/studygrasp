// Foutanalyse + herstelvragen. Twee gedeelde bouwstenen voor quiz- en
// tentamenmodus:
//  1) errorChip(type): benoemt wélk type fout is gemaakt (begripsverwarring,
//     detail vergeten, formule verkeerd toegepast, vraag verkeerd gelezen,
//     verband gemist). Komt gratis mee met de AI-beoordeling.
//  2) recoveryBlock(params): een "Oefen deze fout"-knop die op verzoek een paar
//     korte herstelvragen genereert die precies díe fout aanpakken. Op verzoek
//     (dus alleen bij een klik) en server-side gecacht per concept + fouttype.
import { el, icon, toast } from "./util.js";
import { t } from "./i18n.js";
import { renderMarkdown } from "./markdown.js";
import { api } from "./api.js";
import { prefs } from "./state.js";

// Markdown zonder omhullende <p> (voor inline gebruik in koppen/regels).
const inlineMd = (s) => renderMarkdown(s || "").replace(/^<p>|<\/p>\s*$/g, "");

// Chip die het fouttype benoemt. Onbekend/leeg type → niets (geen chip).
export function errorChip(type) {
  if (!type) return null;
  return el("span", { class: "chip err-chip" }, icon("target", "sm"), t("err_" + type));
}

// "Oefen deze fout"-knop met inline herstelvragen. params:
// { file_hash, concept, error_type, question, model_answer, student_answer, page_index }
export function recoveryBlock(params) {
  const wrap = el("div", { class: "recovery-wrap" });
  const btn = el("button", { class: "btn ghost", style: "font-size:12.5px" },
    icon("sparkle", "sm"), t("practice_mistake"));
  wrap.append(btn);

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.replaceChildren(
      el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }),
      t("recovery_loading"));
    try {
      const res = await api.quizRecovery({ ...params, language: prefs.language });
      const qs = res.questions || [];
      if (!qs.length) throw new Error(t("no_questions"));
      btn.remove();
      wrap.append(renderRecovery(qs));
    } catch (err) {
      toast(err.message, "err", 5000);
      btn.disabled = false;
      btn.replaceChildren(icon("sparkle", "sm"), t("practice_mistake"));
    }
  });
  return wrap;
}

// De herstelvragen: elk vraag + een "toon antwoord"-onthuller met modelantwoord.
// Bewust geen nieuwe beoordelingslus (dat zou extra tokens kosten) — dit is
// gerichte oefening met direct het uitgewerkte antwoord erbij.
function renderRecovery(questions) {
  const box = el("div", { class: "recovery-box" },
    el("div", { class: "grade-head" }, icon("target"), t("recovery_title")));
  questions.forEach((q, i) => {
    const answer = el("div", { class: "md recovery-answer", style: "display:none",
      html: renderMarkdown(q.model_answer || "—") });
    const reveal = el("button", { class: "btn ghost", style: "font-size:12px;margin-top:8px" },
      icon("check", "sm"), t("reveal_answer"));
    reveal.addEventListener("click", () => { answer.style.display = ""; reveal.remove(); });
    box.append(el("div", { class: "recovery-q" },
      el("div", { class: "recovery-q-text" }, `${i + 1}. `, el("span", { html: inlineMd(q.question) })),
      reveal, answer,
    ));
  });
  return box;
}
