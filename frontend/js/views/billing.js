// Volwaardige plan- en factureringspagina. Betalen is nog niet aangesloten;
// daarom toont deze pagina de echte productkeuzes zonder een werkende checkout
// te suggereren.
import { api } from "../api.js";
import { navigate } from "../app.js";
import { t } from "../i18n.js";
import { el, icon, brandMark, toast } from "../util.js";

function normalizedPlan(plan = "free") {
  return ({ plus: "premium", pro: "ultra", unlimited: "ultra" })[plan] || plan;
}

function planLabel(plan = "free") {
  const value = normalizedPlan(plan);
  return t(["owner", "premium", "ultra"].includes(value)
    ? `account_plan_${value}` : "account_plan_free");
}

export function renderBilling(root, user) {
  const isOwner = user?.plan === "owner";
  const current = normalizedPlan(user?.plan);
  const usageValue = el("strong", {}, t("account_usage_loading"));
  const plans = [
    ["free", t("account_plan_free"), t("plan_free_desc"), t("plan_price_free"), 200],
    ["premium", t("account_plan_premium"), t("plan_premium_desc"), t("plan_price_premium"), 1000],
    ["ultra", t("account_plan_ultra"), t("plan_ultra_desc"), t("plan_price_ultra"), 2000],
  ];

  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("left")),
    brandMark(),
    el("div", { class: "spacer" }),
  );

  const overview = el("section", { class: "billing-overview" },
    el("div", { class: "billing-overview-main" },
      el("span", { class: "billing-eyebrow" }, t("plan_current")),
      el("div", { class: "billing-current-line" },
        el("h2", { class: isOwner ? "owner-plan-title" : "" }, planLabel(user?.plan)),
      ),
      el("p", {}, isOwner ? t("plan_owner_note") : t("plan_choose_sub")),
    ),
    el("div", { class: "billing-usage" },
      el("span", {}, t("account_usage")),
      usageValue,
    ),
  );

  const cards = plans.map(([id, label, description, price, credits]) => {
    const isCurrent = id === current;
    return el("article", { class: `plan-card${isCurrent ? " current" : ""}` },
      el("div", { class: "plan-card-top" },
        el("strong", {}, label),
        isCurrent ? el("span", { class: "plan-current" }, t("plan_current")) : null,
      ),
      el("div", { class: "plan-price" }, price),
      el("div", { class: "plan-credits" }, t("plan_credits_month", { credits })),
      el("p", {}, description),
      el("button", {
        class: `btn ${isCurrent || isOwner ? "ghost" : "primary"}`,
        disabled: isCurrent || isOwner,
        onclick: () => toast(t("upgrade_soon"), "info", 4000),
      }, isOwner ? t("plan_owner_active") : isCurrent ? t("plan_current") : t("plan_available_soon")),
    );
  });

  const inner = el("div", { class: "content-inner billing-inner" },
    el("div", { class: "billing-title-row" },
      el("div", {},
        el("h1", { class: "page-title" }, t("account_plan_billing")),
        el("p", { class: "page-sub" }, t("plan_choose_sub")),
      ),
    ),
    overview,
    el("section", { class: "billing-plans" },
      el("h2", {}, t("plan_choose_title")),
      el("div", { class: "plans-grid" }, ...cards),
      el("p", { class: "plan-credit-rules" }, t("plan_credit_rules")),
    ),
    el("section", { class: "billing-invoices" },
      el("div", {}, el("h2", {}, t("billing_invoices_title")), el("p", {}, t("billing_invoices_empty"))),
      el("span", {}, t("plan_available_soon")),
    ),
  );

  root.append(topbar, el("main", { class: "content-page billing-page" }, inner));

  api.usage().then((data) => {
    usageValue.textContent = !data.enabled || data.limit == null
      ? t("account_usage_unlimited")
      : t("account_usage_remaining", { remaining: data.remaining, limit: data.limit });
  }).catch(() => { usageValue.textContent = t("account_usage_unavailable"); });
}
