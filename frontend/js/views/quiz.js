// Overhoormodus: instellen → vragen beantwoorden (MC direct nagekeken,
// open vragen door de AI) → eindscore met overzicht.
import { api } from "../api.js";
import { el, icon, toast } from "../util.js";
import { prefs } from "../state.js";
import { t } from "../i18n.js";
import { renderMarkdown } from "../markdown.js";
import { study } from "../stats.js";
import { navigate } from "../app.js";

export function mountQuiz(main, ctx) {
  const page = el("div", { class: "content-page" });
  main.append(page);
  showSetup(page, ctx);
}

/* ---------- stap 1: instellen ---------- */
function showSetup(page, ctx) {
  const { hash } = ctx;

  let scope = "doc";      // "doc" | "page"
  let qType = "mixed";
  let difficulty = "mixed";
  let count = 8;
  let fresh = false;      // true = eerdere vragenset negeren en nieuwe genereren

  const seg = (options, value, onPick) => {
    const wrap = el("div", { class: "seg", style: "width:100%" });
    for (const [val, label] of options) {
      const b = el("button", { class: val === value ? "on" : "", style: "flex:1", onclick: () => {
        onPick(val);
        wrap.querySelectorAll("button").forEach(x => x.classList.remove("on"));
        b.classList.add("on");
      } }, label);
      wrap.append(b);
    }
    return wrap;
  };

  const countVal = el("span", { class: "chip accent" }, t("n_questions", { n: count }));
  const slider = el("input", { type: "range", min: "3", max: "20", value: String(count), style: "width:100%" });
  slider.addEventListener("input", () => { count = +slider.value; countVal.textContent = t("n_questions", { n: count }); });

  const startBtn = el("button", { class: "btn primary lg", style: "align-self:flex-start" }, icon("play", "sm"), t("start_quiz"));
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    startBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }), t("generating_qs"));
    try {
      const data = await api.quizGenerate({
        file_hash: hash,
        page_index: scope === "page" ? ctx.page : null,
        count,
        question_type: qType,
        difficulty,
        language: prefs.language,
        force_refresh: fresh,
      });
      if (!data.questions?.length) throw new Error(t("no_questions"));
      runQuiz(page, ctx, data.questions);
    } catch (err) {
      toast(err.message, "err", 5000);
      startBtn.disabled = false;
      startBtn.replaceChildren(icon("play", "sm"), t("start_quiz"));
    }
  });

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("quiz"), t("tab_quiz")),
    el("p", { class: "page-sub" }, t("quiz_sub")),
    el("div", { class: "setup-card" },
      el("div", { class: "setup-row" },
        el("label", {}, t("scope_label")),
        seg([["doc", t("scope_doc")], ["page", t("scope_page", { n: ctx.page + 1 })]], scope, (v) => scope = v)),
      el("div", { class: "setup-row" },
        el("label", {}, t("qtype_label")),
        seg([["mixed", t("mix")], ["mc", t("mc")], ["open", t("open_qs")]], qType, (v) => qType = v)),
      el("div", { class: "setup-row" },
        el("label", {}, t("diff_label")),
        seg([["mixed", t("mix")], ["easy", t("easy")], ["medium", t("medium")], ["hard", t("hard")]], difficulty, (v) => difficulty = v)),
      el("div", { class: "setup-row" },
        el("div", { style: "display:flex;justify-content:space-between;align-items:center" }, el("label", {}, t("count_label")), countVal),
        slider),
      el("label", { style: "display:flex;align-items:center;gap:9px;font-size:13px;color:var(--text-soft);cursor:pointer" },
        el("input", { type: "checkbox", onchange: (e) => fresh = e.target.checked }),
        t("fresh_label")),
      startBtn,
    ),
  ));
}

/* ---------- stap 2: vragen ---------- */
function runQuiz(page, ctx, questions) {
  const { hash } = ctx;
  const results = []; // { q, kind:"mc"|"open", correct|verdict, score, answerText }
  let idx = 0;

  const diffLabel = { easy: t("easy").toLowerCase(), medium: t("medium").toLowerCase(), hard: t("hard").toLowerCase() };

  function showQuestion() {
    if (idx >= questions.length) { showResults(page, ctx, questions, results); return; }
    const q = questions[idx];
    const fill = el("div", { class: "progress-fill", style: `width:${(idx / questions.length) * 100}%` });

    const card = el("div", { class: "q-card" },
      el("div", { class: "q-meta" },
        el("span", { class: "chip" }, q.type === "mc" ? t("chip_mc") : t("chip_open")),
        el("span", { class: `chip ${q.difficulty === "hard" ? "red" : q.difficulty === "easy" ? "green" : "amber"}` }, diffLabel[q.difficulty] || q.difficulty),
        q.page_index != null ? el("span", { class: "chip" }, t("slide_chip", { n: q.page_index + 1 })) : null,
      ),
      el("div", { class: "q-text", html: renderMarkdown(q.question) }),
    );

    const actions = el("div", { class: "quiz-actions" },
      el("span", { class: "spacer" }),
      el("button", { class: "btn ghost", onclick: () => { results.push({ q, kind: "skip" }); idx++; showQuestion(); } }, t("skip")),
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
        results.push({ q, kind: "mc", correct, score: correct ? 100 : 0, answerText: q.options[i] });
        study.recordScore(hash, q.page_index, correct ? 100 : 0);
        card.append(el("div", { class: `grade-box ${correct ? "correct" : "incorrect"}` },
          el("div", { class: "grade-head" }, icon(correct ? "check" : "x"),
            correct ? t("correct_head") : t("wrong_head", { a: String.fromCharCode(65 + (q.correct_option ?? 0)) })),
        ));
        nextButton(actions);
      });
      opts.append(btn);
    });
    card.append(opts);
  }

  // Open vragen: zelf nakijken tegen het modelantwoord — instant, zoals bij
  // flashcards. AI-nakijken duurde 20s+ per vraag en haalde het tempo eruit;
  // het blijft beschikbaar als optionele check bij twijfel.
  function mountOpen(card, actions, q) {
    const input = el("textarea", { class: "field", rows: "4", placeholder: t("open_ph") });
    const revealBtn = el("button", { class: "btn primary" }, icon("check", "sm"), t("reveal_answer"));
    const revealRow = el("div", { style: "margin-top:12px" }, revealBtn);
    card.append(input, revealRow);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); revealBtn.click(); }
    });

    revealBtn.addEventListener("click", () => {
      input.disabled = true;
      revealRow.remove();

      const finish = (correct) => {
        results.push({ q, kind: "open", verdict: correct ? "correct" : "incorrect",
                       score: correct ? 100 : 0, answerText: input.value.trim() });
        study.recordScore(hash, q.page_index, correct ? 100 : 0);
        idx++;
        showQuestion(); // direct door — geen extra klik nodig
      };

      const goodBtn = el("button", { class: "btn self-good" }, icon("check", "sm"), t("self_correct"));
      const wrongBtn = el("button", { class: "btn self-wrong" }, icon("x", "sm"), t("self_wrong"));
      const aiBtn = el("button", { class: "btn ghost", style: "font-size:12px" }, icon("sparkle", "sm"), t("ai_check"));
      goodBtn.addEventListener("click", () => finish(true));
      wrongBtn.addEventListener("click", () => finish(false));
      if (!input.value.trim()) aiBtn.style.display = "none"; // niets om na te kijken

      const selfRow = el("div", {},
        el("p", { style: "margin:12px 0 8px;font-size:12.5px;color:var(--muted)" }, t("self_prompt")),
        el("div", { style: "display:flex;gap:9px;flex-wrap:wrap;align-items:center" },
          goodBtn, wrongBtn, el("span", { style: "flex:1" }), aiBtn),
      );
      const box = el("div", { class: "grade-box", style: "margin-top:14px" },
        el("div", { class: "grade-head" }, icon("book"), t("model_answer_label")),
        el("div", { class: "md", html: renderMarkdown(q.model_answer || "—") }),
        selfRow,
      );
      card.append(box);

      aiBtn.addEventListener("click", async () => {
        goodBtn.disabled = wrongBtn.disabled = aiBtn.disabled = true;
        aiBtn.replaceChildren(el("span", { class: "spinner", style: "width:13px;height:13px;border-width:2px" }), t("ai_grading"));
        try {
          const res = await api.quizGrade({
            file_hash: hash,
            question: q.question,
            student_answer: input.value.trim(),
            model_answer: q.model_answer || null,
            page_index: q.page_index,
            language: prefs.language,
          });
          results.push({ q, kind: "open", verdict: res.verdict, score: res.score, answerText: input.value.trim() });
          study.recordScore(hash, q.page_index, res.score);
          selfRow.remove();
          const heads = { correct: ["check", t("correct_head")], partial: ["alert", t("partial_head")], incorrect: ["x", t("incorrect_head")] };
          const [ic, label] = heads[res.verdict] || heads.partial;
          card.append(el("div", { class: `grade-box ${res.verdict}`, style: "margin-top:12px" },
            el("div", { class: "grade-head" }, icon(ic), `${label} · ${res.score}/100`),
            el("div", { class: "md", html: renderMarkdown(res.feedback || "") }),
          ));
          nextButton(actions);
        } catch (err) {
          toast(err.message, "err", 5000);
          goodBtn.disabled = wrongBtn.disabled = aiBtn.disabled = false;
          aiBtn.replaceChildren(icon("sparkle", "sm"), t("ai_check"));
        }
      });
    });
  }

  showQuestion();
}

/* ---------- stap 3: resultaat ---------- */
function showResults(page, ctx, questions, results) {
  const answered = results.filter(r => r.kind !== "skip");
  const avg = answered.length ? Math.round(answered.reduce((s, r) => s + (r.score || 0), 0) / answered.length) : 0;
  const color = avg >= 75 ? "var(--green)" : avg >= 50 ? "var(--amber)" : "var(--red)";
  const R = 56, C = 2 * Math.PI * R;

  const verdictIcon = (r) => {
    if (r.kind === "skip") return ["right", "var(--muted)", t("skipped")];
    if (r.kind === "mc") return r.correct ? ["check", "var(--green)", t("lbl_good")] : ["x", "var(--red)", t("lbl_wrong")];
    return { correct: ["check", "var(--green)", `${r.score}/100`], partial: ["alert", "var(--amber)", `${r.score}/100`], incorrect: ["x", "var(--red)", `${r.score}/100`] }[r.verdict] || ["alert", "var(--amber)", ""];
  };

  // dia's waar fouten of half-goede antwoorden bij zaten → gerichte herhaling
  const weakPages = [...new Set(results
    .filter(r => (r.kind === "mc" && !r.correct) || (r.kind === "open" && r.verdict !== "correct"))
    .map(r => r.q.page_index)
    .filter(p => p != null))].sort((a, b) => a - b);

  page.replaceChildren(el("div", { class: "content-inner narrow" },
    el("div", { class: "score-hero" },
      el("div", { class: "score-ring" },
        el("div", { html:
          `<svg width="132" height="132" viewBox="0 0 132 132">
            <circle cx="66" cy="66" r="${R}" fill="none" stroke="var(--surface-3)" stroke-width="11"/>
            <circle cx="66" cy="66" r="${R}" fill="none" stroke="${color}" stroke-width="11" stroke-linecap="round"
              stroke-dasharray="${C}" stroke-dashoffset="${C * (1 - avg / 100)}" style="transition:stroke-dashoffset 1s var(--ease)"/>
          </svg>` }),
        el("div", { class: "val" }, `${avg}%`),
      ),
      el("h2", { style: "margin:0 0 6px;font-size:20px" },
        avg >= 75 ? t("res_strong") : avg >= 50 ? t("res_ok") : t("res_weak")),
      el("p", { style: "margin:0;color:var(--muted);font-size:13.5px" },
        t("answered_frac", { a: answered.length, b: questions.length })),
    ),
    el("div", { class: "quiz-actions", style: "justify-content:center" },
      el("button", { class: "btn primary", onclick: () => showSetup(page, ctx) }, icon("refresh", "sm"), t("new_quiz")),
    ),
    weakPages.length ? el("div", { class: "grade-box partial", style: "margin-top:20px" },
      el("div", { class: "grade-head" }, icon("target"), t("weak_title")),
      el("div", { style: "display:flex;gap:8px;flex-wrap:wrap;margin-top:4px" },
        ...weakPages.map(p => el("button", { class: "btn", style: "font-size:12.5px;padding:5px 12px",
          onclick: () => navigate(`#/doc/${ctx.hash}/study/${p}`) }, icon("book", "sm"), t("slide_n", { n: p + 1 }))),
      ),
      el("p", { style: "margin:10px 0 0;font-size:12px;color:var(--muted)" }, t("weak_note")),
    ) : null,
    el("div", { class: "review-list" },
      ...results.map((r) => {
        const [ic, col, lbl] = verdictIcon(r);
        return el("div", { class: "review-item" },
          el("span", { style: `color:${col}` }, icon(ic)),
          el("div", { style: "flex:1;min-width:0" },
            el("div", { style: "font-weight:600", html: renderMarkdown(r.q.question).replace(/^<p>|<\/p>\s*$/g, "") },),
            el("div", { style: "color:var(--muted);font-size:12.5px;margin-top:3px" },
              lbl,
              r.q.page_index != null ? el("button", { class: "btn ghost", style: "font-size:11.5px;padding:2px 8px;margin-left:8px", onclick: () => navigate(`#/doc/${ctx.hash}/study/${r.q.page_index}`) }, t("view_slide", { n: r.q.page_index + 1 })) : null,
            ),
          ),
        );
      }),
    ),
  ));
}
