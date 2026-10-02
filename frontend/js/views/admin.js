// Afgeschermd eigenaarsoverzicht. De route is alleen een UI-gemak; de backend
// voert de echte owner-controle uit voordat accountgegevens worden teruggegeven.
import { api } from "../api.js";
import { navigate } from "../app.js";
import { t, uiLocale } from "../i18n.js";
import { el, icon, brandMark } from "../util.js";

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
        el("span", { class: `chip${account.plan === "owner" ? " accent" : ""}` }, planLabel(account.plan))),
      el("div", { class: "admin-account-cell", dataset: { label: t("admin_usage") } }, usageLabel(account.usage)),
    )));
    empty.hidden = visible.length > 0;
  };

  const search = el("input", {
    class: "field admin-account-search", type: "search", placeholder: t("admin_search"),
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
      el("span", {}, t("admin_plan")), el("span", {}, t("admin_usage"))),
    rows,
    empty,
  ));
}
