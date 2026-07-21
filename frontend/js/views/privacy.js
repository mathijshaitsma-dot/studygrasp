// Privacybeleid: eenvoudige, eerlijke uitleg — geen juridisch jargon. Er zijn
// nog geen accounts, dus ook geen naam/e-mail/betaalgegevens om te melden.
import { el, icon } from "../util.js";
import { t } from "../i18n.js";
import { navigate } from "../app.js";

export function renderPrivacy(root) {
  const topbar = el("div", { class: "topbar" },
    el("button", { class: "btn ghost icon-btn", title: t("to_home"), onclick: () => navigate("#/") }, icon("home")),
    el("div", { class: "brand" }, el("span", { class: "logo" }, icon("book")), "StudyCopilot"),
    el("div", { class: "spacer" }),
  );

  const inner = el("div", { class: "content-inner narrow" },
    el("h1", { class: "page-title" }, icon("book"), t("privacy_title")),
    el("p", { class: "page-sub" }, t("privacy_intro")),
    privacySection(t("privacy_data_h"), t("privacy_data_b")),
    privacySection(t("privacy_ai_h"), t("privacy_ai_b")),
    privacySection(t("privacy_accounts_h"), t("privacy_accounts_b")),
    privacySection(t("privacy_retention_h"), t("privacy_retention_b")),
    privacySection(t("privacy_share_h"), t("privacy_share_b")),
    privacySection(t("privacy_contact_h"), t("privacy_contact_b")),
  );

  root.append(topbar, el("div", { class: "content-page" }, inner));
}

function privacySection(heading, body) {
  return el("div", { style: "margin-top:26px" },
    el("h3", { style: "margin:0 0 6px" }, heading),
    el("p", { style: "color:var(--text-soft);margin:0;line-height:1.6" }, body),
  );
}
