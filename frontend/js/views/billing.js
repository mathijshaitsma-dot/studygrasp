// Plan- en factureringspagina. Checkout en het klantenportaal worden altijd
// server-side aangemaakt; geheime Stripe-sleutels komen nooit in de browser.
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

async function redirectToBilling(action, button) {
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = t("billing_redirecting");
  try {
    const data = await action();
    if (!data?.url) throw new Error(t("billing_unavailable"));
    location.assign(data.url);
  } catch (err) {
    button.disabled = false;
    button.textContent = oldText;
    toast(err.message || t("billing_unavailable"), "error", 5000);
  }
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
  const planButtons = new Map();

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
    const button = el("button", {
      class: `btn ${isCurrent || isOwner ? "ghost" : "primary"}`,
      disabled: true,
    }, isOwner ? t("plan_owner_active") : isCurrent ? t("plan_current") : t("billing_loading"));
    planButtons.set(id, button);
    return el("article", { class: `plan-card${isCurrent ? " current" : ""}` },
      el("div", { class: "plan-card-top" },
        el("strong", {}, label),
        isCurrent ? el("span", { class: "plan-current" }, t("plan_current")) : null,
      ),
      el("div", { class: "plan-price" }, price),
      el("div", { class: "plan-credits" }, t("plan_credits_month", { credits })),
      el("p", {}, description),
      button,
    );
  });

  const invoicesText = el("p", {}, t("billing_invoices_empty"));
  const invoicesAction = el("span", {}, t("billing_loading"));
  const invoices = el("section", { class: "billing-invoices" },
    el("div", {}, el("h2", {}, t("billing_invoices_title")), invoicesText),
    invoicesAction,
  );

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
    invoices,
  );

  root.append(topbar, el("main", { class: "content-page billing-page" }, inner));

  const checkoutResult = new URLSearchParams(location.search).get("checkout");
  if (checkoutResult) {
    history.replaceState({}, "", `${location.pathname}${location.hash}`);
    toast(t(checkoutResult === "success" ? "billing_payment_processing" : "billing_payment_cancelled"), "info", 5000);
    if (checkoutResult === "success") {
      // De Stripe-webhook kan een paar seconden later binnenkomen dan de
      // terugkeer naar StudyGrasp. Ververs zodra het plan is bijgewerkt.
      let attempts = 0;
      const refreshWhenActivated = async () => {
        attempts += 1;
        try {
          const result = await api.me();
          if (normalizedPlan(result?.user?.plan) !== current) {
            location.reload();
            return;
          }
        } catch (_) {
          // De gewone foutmelding blijft beschikbaar via een handmatige reload.
        }
        if (attempts < 12) setTimeout(refreshWhenActivated, 1000);
      };
      setTimeout(refreshWhenActivated, 500);
    }
  }

  api.usage().then((data) => {
    usageValue.textContent = !data.enabled || data.limit == null
      ? t("account_usage_unlimited")
      : t("account_usage_remaining", { remaining: data.remaining, limit: data.limit });
  }).catch(() => { usageValue.textContent = t("account_usage_unavailable"); });

  api.billingStatus().then((status) => {
    const hasPaidSubscription = status.subscription || ["premium", "ultra"].includes(current);
    for (const [id, button] of planButtons) {
      if (isOwner) {
        button.disabled = true;
        button.textContent = t("plan_owner_active");
      } else if (id === current) {
        button.disabled = true;
        button.textContent = t("plan_current");
      } else if (!status.configured) {
        button.disabled = true;
        button.textContent = t("billing_unavailable");
      } else if (hasPaidSubscription || id === "free") {
        button.disabled = !status.customer;
        button.textContent = t("billing_manage");
        button.onclick = () => redirectToBilling(() => api.billingPortal(), button);
      } else {
        button.disabled = false;
        button.textContent = t("billing_choose_plan", { plan: planLabel(id) });
        button.onclick = () => redirectToBilling(() => api.billingCheckout(id), button);
      }
    }

    invoicesText.textContent = status.customer ? t("billing_portal_desc") : t("billing_invoices_empty");
    if (status.customer) {
      const manage = el("button", { class: "btn ghost" }, t("billing_manage"));
      manage.onclick = () => redirectToBilling(() => api.billingPortal(), manage);
      invoicesAction.replaceWith(manage);
    } else {
      invoicesAction.textContent = status.configured ? t("billing_no_payments") : t("billing_unavailable");
    }
  }).catch(() => {
    for (const button of planButtons.values()) {
      if (![t("plan_current"), t("plan_owner_active")].includes(button.textContent)) {
        button.disabled = true;
        button.textContent = t("billing_unavailable");
      }
    }
    invoicesAction.textContent = t("billing_unavailable");
  });
}
