// Afgeschermd eigenaarsoverzicht. De route is alleen een UI-gemak; de backend
// voert de echte owner-controle uit voordat accountgegevens worden teruggegeven.
import { api } from "../api.js";
import { navigate } from "../app.js";
import { t, uiLocale } from "../i18n.js";
import { el, icon, brandMark, openModal, confirmDialog, toast } from "../util.js";

function planLabel(plan = "free") {
  const normalized = ({ plus: "premium", pro: "ultra", unlimited: "ultra" })[plan] || plan;
  return t(["owner", "premium", "ultra"].includes(normalized)
    ? `account_plan_${normalized}` : "account_plan_free");
}

function registeredAt(value) {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString(uiLocale(), {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function usageLabel(usage = {}) {
  if (usage.limit == null) return t("account_usage_unlimited");
  return `${usage.used || 0} / ${usage.limit}`;
}

function grantUntil(account) {
  const expires = account.plan_grant?.expires_at;
  return expires ? t("admin_temporary_until", { date: registeredAt(expires) }) : "";
}

export async function renderAdmin(root) {
  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("left")),
    brandMark(),
    el("div", { class: "spacer" }),
  );
  const page = el("div", { class: "content-page" },
    el("div", { style: "display:grid;place-items:center;padding:70px" }, el("div", { class: "spinner" })));
  root.append(topbar, page);

  let data;
  try {
    data = await api.adminAccounts();
  } catch (err) {
    page.replaceChildren(el("div", { class: "content-inner narrow" },
      el("div", { class: "md-error" }, icon("alert"), el("div", {}, err.message))));
    return;
  }

  const accounts = data.accounts || [];
  const rows = el("div", { class: "admin-account-list" });
  const empty = el("div", { class: "empty-state", hidden: true }, el("p", {}, t("admin_no_results")));
  let search;

  const replaceAccount = (updated) => {
    const index = accounts.findIndex(account => account.id === updated.id);
    if (index >= 0) accounts[index] = updated;
    renderRows(search?.value || "");
  };

  const openPlanGrant = (account) => {
    let close;
    const plan = el("select", { class: "field", "aria-label": t("admin_grant_plan") },
      el("option", { value: "premium" }, planLabel("premium")),
      el("option", { value: "ultra" }, planLabel("ultra")),
    );
    if (account.plan_grant?.plan) plan.value = account.plan_grant.plan;
    else if (account.base_plan === "premium") plan.value = "ultra";
    const amount = el("input", { class: "field", type: "number", min: "1", max: "3650", value: "30",
      "aria-label": t("admin_duration") });
    const unit = el("select", { class: "field", "aria-label": t("admin_duration_unit") },
      el("option", { value: "hours" }, t("admin_hours")),
      el("option", { value: "days", selected: true }, t("admin_days")),
      el("option", { value: "weeks" }, t("admin_weeks")),
      el("option", { value: "months" }, t("admin_months")),
    );
    const syncDurationLimit = () => {
      const limits = { hours: 3650, days: 3650, weeks: 521, months: 120 };
      amount.max = String(limits[unit.value]);
      if (+amount.value > limits[unit.value]) amount.value = String(limits[unit.value]);
    };
    unit.addEventListener("change", syncDurationLimit);
    syncDurationLimit();
    const save = el("button", { class: "btn primary", onclick: async () => {
      const durationValue = Number.parseInt(amount.value, 10);
      if (!Number.isInteger(durationValue) || durationValue < 1) { amount.focus(); return; }
      save.disabled = true;
      try {
        const result = await api.adminGrantPlan(account.id, {
          plan: plan.value, duration_value: durationValue, duration_unit: unit.value,
        });
        replaceAccount(result.account);
        close();
        toast(t("admin_grant_done", { email: account.email, plan: planLabel(plan.value) }), "ok", 5000);
      } catch (err) {
        save.disabled = false;
        toast(err.message, "err", 5000);
      }
    } }, icon("check", "sm"), t("admin_grant_confirm"));
    close = openModal(el("div", { class: "confirm-card admin-grant-card" },
      el("div", { class: "confirm-icon" }, icon("sparkle")),
      el("h3", {}, t("admin_grant_title")),
      el("p", {}, t("admin_grant_body", { email: account.email })),
      el("label", {}, t("admin_grant_plan"), plan),
      el("label", {}, t("admin_duration"),
        el("div", { class: "admin-duration-row" }, amount, unit)),
      el("div", { class: "confirm-foot" },
        el("button", { class: "btn", onclick: () => close() }, t("cancel")), save),
    ), { center: true, small: true, label: t("admin_grant_title") });
  };

  const revokeGrant = async (account) => {
    const ok = await confirmDialog({
      title: t("admin_revoke_title"), body: t("admin_revoke_body", { email: account.email }),
    });
    if (!ok) return;
    try {
      const result = await api.adminRevokePlan(account.id);
      replaceAccount(result.account);
      toast(t("admin_revoke_done"), "ok");
    } catch (err) { toast(err.message, "err", 5000); }
  };

  const renderRows = (query = "") => {
    const needle = query.trim().toLowerCase();
    const visible = accounts.filter(account => !needle ||
      account.email.toLowerCase().includes(needle) || planLabel(account.plan).toLowerCase().includes(needle));
    rows.replaceChildren(...visible.map(account => el("article", { class: "admin-account-row" },
      el("div", { class: "admin-account-identity" },
        el("strong", {}, account.email),
        el("span", {}, account.signup_method === "google" ? t("admin_via_google") : t("admin_via_email"))),
      el("div", { class: "admin-account-cell", dataset: { label: t("admin_registered") } }, registeredAt(account.created_at)),
      el("div", { class: "admin-account-cell", dataset: { label: t("admin_plan") } },
        el("div", { class: "admin-plan-stack" },
          el("span", { class: `chip${account.plan === "owner" ? " accent" : ""}` }, planLabel(account.plan)),
          account.plan_grant ? el("small", {}, grantUntil(account)) : null)),
      el("div", { class: "admin-account-cell", dataset: { label: t("admin_usage") } }, usageLabel(account.usage)),
      el("div", { class: "admin-account-cell admin-account-actions", dataset: { label: t("admin_access") } },
        account.plan === "owner" ? "—" : account.plan_grant
          ? el("div", { class: "admin-action-buttons" },
              el("button", { class: "btn ghost", onclick: () => openPlanGrant(account) }, t("admin_change")),
              el("button", { class: "btn ghost", onclick: () => revokeGrant(account) }, t("admin_revoke")))
          : account.base_plan === "ultra" ? t("admin_no_upgrade")
          : el("button", { class: "btn ghost", onclick: () => openPlanGrant(account) }, icon("sparkle", "sm"), t("admin_grant"))),
    )));
    empty.hidden = visible.length > 0;
  };

  search = el("input", {
    class: "field admin-account-search", type: "search", placeholder: t("admin_search"),
    "aria-label": t("admin_search"),
    oninput: (event) => renderRows(event.target.value),
  });
  renderRows();

  page.replaceChildren(el("div", { class: "content-inner admin-inner" },
    el("h1", { class: "page-title" }, icon("settings"), t("admin_title")),
    el("p", { class: "page-sub" }, t("admin_sub")),
    el("div", { class: "admin-summary" },
      el("span", { class: "admin-summary-number" }, String(data.total ?? accounts.length)),
      el("span", {}, t("admin_total_accounts"))),
    search,
    el("div", { class: "admin-account-head" },
      el("span", {}, t("admin_account")), el("span", {}, t("admin_registered")),
      el("span", {}, t("admin_plan")), el("span", {}, t("admin_usage")), el("span", {}, t("admin_access"))),
    rows,
    empty,
  ));
}
