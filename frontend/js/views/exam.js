// Tentamenmodus: oefententamen op echt tentamenniveau (toepassen, rekenen,
// combineren) over één document of een hele map. Fouten worden per concept
// bijgehouden; de backend bouwt daar een herhaalplanning van.
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import { study } from "../stats.js";
import { navigate } from "../app.js";
import { errorChip, recoveryBlock } from "../recovery.js";

// ctx: { scope: {file_hash} | {folder_id}, name }  (naam van document of map)
export function mountExam(main, ctx) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  showIntro(page, ctx);
}

/* ---------- stap 1: intro + planning ---------- */
async function showIntro(page, ctx) {
  let count = 10;
  let fresh = false;

  const countVal = el("span", { class: "chip accent" }, t("n_questions", { n: count }));
  const slider = el("input", { type: "range", min: "5", max: "20", value: String(count), style: "width:100%" });
  slider.addEventListener("input", () => { count = +slider.value; countVal.textContent = t("n_questions", { n: count }); });

  const startBtn = el("button", { class: "btn primary lg", style: "align-self:flex-start" }, icon("play", "sm"), t("start_exam"));
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    startBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }), t("generating_exam"));
    try {
      const data = await api.examGenerate({ ...ctx.scope, count, language: prefs.language, force_refresh: fresh });
      if (!data.questions?.length) throw new Error(t("no_questions"));
      runExam(page, ctx, data.questions);
    } catch (err) {
      toast(err.message, "err", 5000);
      startBtn.disabled = false;
      startBtn.replaceChildren(icon("play", "sm"), t("start_exam"));
    }
  });

  const planArea = el("div", {});

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("cap"), t("tab_exam")),
    el("p", { class: "page-sub" }, t("exam_sub", { name: ctx.name })),
    el("div", { class: "setup-card" },
      el("div", { class: "setup-row" },
        el("div", { style: "display:flex;justify-content:space-between;align-items:center" }, el("label", {}, t("count_label")), countVal),
        slider),
      el("label", { style: "display:flex;align-items:center;gap:9px;font-size:13px;color:var(--text-soft);cursor:pointer" },
        el("input", { type: "checkbox", onchange: (e) => fresh = e.target.checked }),
        t("fresh_label")),
      startBtn,
    ),
    planArea,
  ));

  // Eerdere pogingen + herhaalplanning (als die er zijn) onder het startblok.
  try {
    const data = await api.examPlan(ctx.scope);
    renderPlanSection(planArea, ctx, data.attempts || [], data.plan);
  } catch { /* nog geen planning of lege map — niets tonen */ }
}

function gradeFromScore(score) {
  // Nederlands cijfer: 100% => 10, 0% => 1.
  return (1 + 9 * (score / 100)).toFixed(1).replace(".", ",");
}

function renderPlanSection(container, ctx, attempts, plan) {
  const hasConcepts = plan && Object.values(plan).some(items => items.length);
  if (!attempts.length && !hasConcepts) {
    container.replaceChildren(
      el("p", { style: "margin-top:18px;font-size:13px;color:var(--muted)" }, t("plan_empty")));
    return;
  }

  const last = attempts[attempts.length - 1];
  const kids = [el("div", { class: "section-title", style: "margin-top:28px" }, t("plan_title"), el("span", { class: "line" }))];

  if (last) {
    kids.push(el("div", { class: "fc-stats" },
      statCard(`${last.score}%`, t("exam_last_score")),
      statCard(gradeFromScore(last.score), t("exam_grade_label"), last.score >= 55 ? "var(--green)" : "var(--red)"),
      statCard(String(attempts.length), t("exam_attempts")),
    ));
  }

  const buckets = [
    ["due_now", t("plan_due_now"), "var(--red)"],
    ["tomorrow", t("plan_tomorrow"), "var(--amber)"],
    ["this_week", t("plan_week"), "var(--accent)"],
    ["later", t("plan_later"), "var(--muted)"],
    ["mastered", t("plan_mastered"), "var(--green)"],
  ];
  for (const [key, label, color] of buckets) {
    const items = plan?.[key] || [];
    if (!items.length) continue;
    kids.push(el("div", { class: "plan-bucket" },
      el("div", { class: "plan-bucket-head" },
        el("span", { class: "plan-dot", style: `background:${color}` }), label,
        el("span", { class: "chip", style: "margin-left:auto" }, String(items.length))),
      ...items.map(item => planRow(item, ctx)),
    ));
  }
  container.replaceChildren(...kids);
}

function planRow(item, ctx) {
  const total = item.right + item.wrong;
  const pct = total ? Math.round(item.mastery * 100) : 0;
  const canJump = item.file_hash && item.page_index != null;
  return el("div", { class: "plan-row" },
    el("div", { style: "flex:1;min-width:0" },
      el("div", { style: "font-weight:600;font-size:13.5px", html: renderMarkdown(item.concept).replace(/^<p>|<\/p>\s*$/g, "") }),
      el("div", { style: "font-size:12px;color:var(--muted);margin-top:2px" },
        t("plan_stats", { a: item.right, b: total, p: pct })),
      item.top_error ? el("div", { style: "margin-top:5px;display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--muted)" },
        t("top_error_label"), errorChip(item.top_error)) : null,
    ),
    el("div", { class: "progress-track", style: "width:70px" },
      el("div", { class: "progress-fill", style: `width:${pct}%` })),
    canJump ? el("button", { class: "btn ghost", style: "font-size:12px;padding:4px 10px",
      onclick: () => navigate(`#/doc/${item.file_hash}/study/${item.page_index}`) },
      icon("book", "sm"), t("slide_n", { n: item.page_index + 1 })) : null,
  );
}

function statCard(num, label, color) {
  return el("div", { class: "stat-card" },
    el("div", { class: "num", style: color ? `color:${color}` : "" }, String(num)),
    el("div", { class: "lbl" }, label));
}

/* ---------- stap 2: vragen ---------- */
function runExam(page, ctx, questions) {
  const results = []; // { q, correct, score, skipped }
  let idx = 0;

  const diffLabel = { easy: t("easy").toLowerCase(), medium: t("medium").toLowerCase(), hard: t("hard").toLowerCase() };

  function recordResult(q, correct, score, errorType = null) {
    results.push({ q, correct, score, error_type: errorType });
    if (q.file_hash) study.recordScore(q.file_hash, q.page_index, score);
  }

  function showQuestion() {
    if (idx >= questions.length) { finishExam(page, ctx, questions, results); return; }
    const q = questions[idx];
    const fill = el("div", { class: "progress-fill", style: `width:${(idx / questions.length) * 100}%` });

    const card = el("div", { class: "q-card" },
      el("div", { class: "q-meta" },
        el("span", { class: "chip" }, q.type === "mc" ? t("chip_mc") : t("chip_open")),
        el("span", { class: `chip ${q.difficulty === "hard" ? "red" : q.difficulty === "easy" ? "green" : "amber"}` }, diffLabel[q.difficulty] || q.difficulty),
        q.concept ? el("span", { class: "chip accent" }, icon("target", "sm"), q.concept) : null,
        q.page_index != null ? el("span", { class: "chip" }, t("slide_chip", { n: q.page_index + 1 })) : null,
      ),
      el("div", { class: "q-text", html: renderMarkdown(q.question) }),
    );

    const actions = el("div", { class: "quiz-actions" },
      el("span", { class: "spacer" }),
      el("button", { class: "btn ghost", onclick: () => { results.push({ q, skipped: true }); idx++; showQuestion(); } }, t("skip")),
    );

    if (q.type === "mc") mountMC(card, actions, q);
    else mountOpen(card, actions, q);

    page.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "quiz-top" },
        el("div", { class: "progress-track" }, fill),
        el("span", { class: "count" }, `${idx + 1} / ${questions.length}`)),
      card, actions,
    ));
  }

  function nextButton(actions) {
    actions.replaceChildren(
      el("span", { class: "spacer" }),
      el("button", { class: "btn primary", onclick: () => { idx++; showQuestion(); } },
        idx + 1 >= questions.length ? t("see_result") : t("next_q"), icon("right", "sm")),
    );
  }

  function explanationBox(q) {
    if (!q.explanation) return null;
    return el("div", { style: "margin-top:10px;font-size:13px;color:var(--text-soft)" },
      el("strong", {}, t("exam_explanation") + " "),
      el("span", { class: "md", style: "display:inline", html: renderMarkdown(q.explanation).replace(/^<p>|<\/p>\s*$/g, "") }));
  }

  function mountMC(card, actions, q) {
    const opts = el("div", { class: "mc-opts" });
    q.options.forEach((opt, i) => {
      const btn = el("button", { class: "mc-opt" },
        el("span", { class: "key" }, String.fromCharCode(65 + i)),
        el("span", { html: renderMarkdown(opt).replace(/^<p>|<\/p>\s*$/g, "") }),
      );
      btn.addEventListener("click", () => {
        const correct = i === q.correct_option;
        opts.querySelectorAll(".mc-opt").forEach((b, j) => {
          b.disabled = true;
          if (j === q.correct_option) b.classList.add("correct");
          else if (j === i && !correct) b.classList.add("wrong");
        });
        recordResult(q, correct, correct ? 100 : 0);
        card.append(el("div", { class: `grade-box ${correct ? "correct" : "incorrect"}` },
          el("div", { class: "grade-head" }, icon(correct ? "check" : "x"),
            correct ? t("correct_head") : t("wrong_head", { a: String.fromCharCode(65 + (q.correct_option ?? 0)) })),
          explanationBox(q),
        ));
        nextButton(actions);
      });
      opts.append(btn);
    });
    card.append(opts);
  }

  // Open vragen: modelantwoord tonen + zelf beoordelen (goed / deels / fout),
  // met optionele AI-check — hetzelfde snelle patroon als de overhoormodus.
  function mountOpen(card, actions, q) {
    const input = el("textarea", { class: "field", rows: "5", placeholder: t("exam_open_ph") });
    const revealBtn = el("button", { class: "btn primary" }, icon("check", "sm"), t("reveal_answer"));
    const revealRow = el("div", { style: "margin-top:12px" }, revealBtn);
    card.append(input, revealRow);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); revealBtn.click(); }
    });

    revealBtn.addEventListener("click", () => {
      input.disabled = true;
      revealRow.remove();

      const advance = () => { idx++; showQuestion(); };
      const finish = (score) => { recordResult(q, score >= 70, score); advance(); };

      const goodBtn = el("button", { class: "btn self-good" }, icon("check", "sm"), t("self_correct"));
      const halfBtn = el("button", { class: "btn" }, icon("alert", "sm"), t("self_partial"));
      const wrongBtn = el("button", { class: "btn self-wrong" }, icon("x", "sm"), t("self_wrong"));
      const aiBtn = el("button", { class: "btn ghost", style: "font-size:12px" }, icon("sparkle", "sm"), t("ai_check"));
      goodBtn.addEventListener("click", () => finish(100));   // goed → meteen door
      halfBtn.addEventListener("click", () => selfWrong(50));  // deels/fout → optioneel analyseren
      wrongBtn.addEventListener("click", () => selfWrong(0));
      if (!input.value.trim()) aiBtn.style.display = "none";

      const selfRow = el("div", {},
        el("p", { style: "margin:12px 0 8px;font-size:12.5px;color:var(--muted)" }, t("self_prompt")),
        el("div", { style: "display:flex;gap:9px;flex-wrap:wrap;align-items:center" },
          goodBtn, halfBtn, wrongBtn, el("span", { style: "flex:1" }), aiBtn),
      );
      const box = el("div", { class: "grade-box", style: "margin-top:14px" },
        el("div", { class: "grade-head" }, icon("book"), t("model_answer_label")),
        el("div", { class: "md", html: renderMarkdown(q.model_answer || "—") }),
        explanationBox(q),
        selfRow,
      );
      card.append(box);

      // Zelf als deels/fout gemarkeerd: fout registreren, maar niet meteen door —
      // bied "Analyseer waarom" aan. Eén AI-aanroep, alleen bij een klik, en zet
      // het fouttype op het resultaat zodat het meetelt in de herhaalplanning.
      // Zonder getypt antwoord (of zonder document) valt er niets te analyseren.
      function selfWrong(score) {
        const result = { q, correct: false, score, error_type: null };
        results.push(result);
        if (q.file_hash) study.recordScore(q.file_hash, q.page_index, score);
        selfRow.remove();

        const partial = score >= 40;
        const analysed = el("div", { class: `grade-box ${partial ? "partial" : "incorrect"}`, style: "margin-top:12px" },
          el("div", { class: "grade-head" }, icon(partial ? "alert" : "x"),
            partial ? t("self_partial") : t("self_wrong")));
        const nextBtn = el("button", { class: "btn primary" },
          idx + 1 >= questions.length ? t("see_result") : t("next_q"), icon("right", "sm"));
        nextBtn.addEventListener("click", advance);
        const analyseBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" },
          icon("sparkle", "sm"), t("analyse_why"));
        const recoverHash = q.file_hash || ctx.scope.file_hash;
        const row = el("div", { style: "display:flex;gap:9px;align-items:center;margin-top:8px" });
        if (input.value.trim() && recoverHash) row.append(analyseBtn);
        row.append(el("span", { style: "flex:1" }), nextBtn);
        analysed.append(row);
        card.append(analysed);

        analyseBtn.addEventListener("click", async () => {
          analyseBtn.disabled = true;
          analyseBtn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("analysing"));
          try {
            const res = await api.quizGrade({
              file_hash: recoverHash, question: q.question,
              student_answer: input.value.trim(), model_answer: q.model_answer || null,
              page_index: q.page_index, language: prefs.language,
            });
            result.error_type = res.error_type || null;  // meegeteld in examAttempt
            if (result.error_type) analysed.querySelector(".grade-head").append(errorChip(result.error_type));
            analysed.insertBefore(el("div", { class: "md", html: renderMarkdown(res.feedback || "") }), row);
            analysed.insertBefore(recoveryBlock({
              file_hash: recoverHash, concept: q.concept || "",
              error_type: result.error_type, question: q.question,
              model_answer: q.model_answer || null, student_answer: input.value.trim(),
              page_index: q.page_index,
            }), row);
            analyseBtn.remove();
          } catch (err) {
            toast(err.message, "err", 5000);
            analyseBtn.disabled = false;
            analyseBtn.replaceChildren(icon("sparkle", "sm"), t("analyse_why"));
          }
        });
      }

      aiBtn.addEventListener("click", async () => {
        goodBtn.disabled = halfBtn.disabled = wrongBtn.disabled = aiBtn.disabled = true;
        aiBtn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("ai_grading"));
        try {
          const res = await api.quizGrade({
            file_hash: q.file_hash || ctx.scope.file_hash,
            question: q.question,
            student_answer: input.value.trim(),
            model_answer: q.model_answer || null,
            page_index: q.page_index,
            language: prefs.language,
          });
          recordResult(q, res.verdict === "correct", res.score, res.error_type || null);
          selfRow.remove();
          const heads = { correct: ["check", t("correct_head")], partial: ["alert", t("partial_head")], incorrect: ["x", t("incorrect_head")] };
          const [ic, label] = heads[res.verdict] || heads.partial;
          const wrong = res.verdict !== "correct";
          const gradeBox = el("div", { class: `grade-box ${res.verdict}`, style: "margin-top:12px" },
            el("div", { class: "grade-head" }, icon(ic), `${label} · ${res.score}/100`,
              wrong ? errorChip(res.error_type) : null),
            el("div", { class: "md", html: renderMarkdown(res.feedback || "") }),
          );
          const recoverHash = q.file_hash || ctx.scope.file_hash;
          if (wrong && recoverHash) {
            gradeBox.append(recoveryBlock({
              file_hash: recoverHash,
              concept: q.concept || "",
              error_type: res.error_type || null,
              question: q.question,
              model_answer: q.model_answer || null,
              student_answer: input.value.trim(),
              page_index: q.page_index,
            }));
          }
          card.append(gradeBox);
          nextButton(actions);
        } catch (err) {
          toast(err.message, "err", 5000);
          goodBtn.disabled = halfBtn.disabled = wrongBtn.disabled = aiBtn.disabled = false;
          aiBtn.replaceChildren(icon("sparkle", "sm"), t("ai_check"));
        }
      });
    });
  }

  showQuestion();
}

/* ---------- stap 3: resultaat + herhaalplanning ---------- */
async function finishExam(page, ctx, questions, results) {
  const answered = results.filter(r => !r.skipped);
  const avg = answered.length ? Math.round(answered.reduce((s, r) => s + (r.score || 0), 0) / answered.length) : 0;
  const color = avg >= 75 ? "var(--green)" : avg >= 55 ? "var(--amber)" : "var(--red)";
  const R = 56, C = 2 * Math.PI * R;

  // Fouten registreren => de backend werkt de herhaalplanning bij.
  let plan = null;
  if (answered.length) {
    try {
      const resp = await api.examAttempt({
        ...ctx.scope,
        results: answered.map(r => ({
          concept: r.q.concept || "",
          file_hash: r.q.file_hash || ctx.scope.file_hash || null,
          page_index: r.q.page_index,
          correct: !!r.correct,
          score: r.score || 0,
          error_type: r.error_type || null,
        })),
      });
      plan = resp.plan;
    } catch (err) { toast(err.message, "err"); }
  }

  const planArea = el("div", {});
  if (plan) {
    renderPlanSection(planArea, ctx, [], plan);
    planArea.prepend(el("p", { style: "margin:22px 0 0;font-size:13px;color:var(--text-soft);text-align:center" },
      icon("target", "sm"), " ", t("exam_plan_updated")));
  }

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("div", { class: "score-hero" },
      el("div", { class: "score-ring" },
        el("div", { html:
          `<svg width="132" height="132" viewBox="0 0 132 132">
            <circle cx="66" cy="66" r="${R}" fill="none" stroke="var(--surface-3)" stroke-width="11"/>
            <circle cx="66" cy="66" r="${R}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
              stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - avg / 100)}" style="transition:stroke-dashoffset 1s var(--ease)"/>
          </svg>` }),
        el("div", { class: "val" }, gradeFromScore(avg)),
      ),
      el("h2", { style: "margin:0 0 6px;font-size:20px" },
        avg >= 75 ? t("exam_res_strong") : avg >= 55 ? t("exam_res_ok") : t("exam_res_weak")),
      el("p", { style: "margin:0;color:var(--muted);font-size:13.5px" },
        t("exam_grade_line", { p: avg, g: gradeFromScore(avg) })),
    ),
    el("div", { class: "quiz-actions", style: "justify-content:center" },
      el("button", { class: "btn primary", onclick: () => showIntro(page, ctx) }, icon("refresh", "sm"), t("exam_again")),
    ),
    planArea,
    el("div", { class: "review-list" },
      ...results.map((r) => {
        const [ic, col, lbl] = r.skipped ? ["right", "var(--muted)", t("skipped")]
          : r.correct ? ["check", "var(--green)", `${r.score}/100`]
          : r.score >= 40 ? ["alert", "var(--amber)", `${r.score}/100`]
          : ["x", "var(--red)", `${r.score}/100`];
        return el("div", { class: "review-item" },
          el("span", { style: `color:${col}` }, icon(ic)),
          el("div", { style: "flex:1;min-width:0" },
            el("div", { style: "font-weight:600", html: renderMarkdown(r.q.question).replace(/^<p>|<\/p>\s*$/g, "") }),
            el("div", { style: "color:var(--muted);font-size:12.5px;margin-top:3px" },
              lbl,
              r.q.concept ? el("span", { class: "chip", style: "margin-left:8px" }, r.q.concept) : null,
              !r.correct && !r.skipped && r.error_type
                ? el("span", { style: "margin-left:8px" }, errorChip(r.error_type)) : null,
              r.q.file_hash && r.q.page_index != null
                ? el("button", { class: "btn ghost", style: "font-size:11.5px;padding:2px 8px;margin-left:8px",
                    onclick: () => navigate(`#/doc/${r.q.file_hash}/study/${r.q.page_index}`) },
                    t("view_slide", { n: r.q.page_index + 1 }))
                : null,
            ),
          ),
        );
      }),
    ),
  ));
}
