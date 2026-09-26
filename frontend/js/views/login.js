// Inloggen / registreren. Eén scherm met een schakelaar, want het verschil is
// klein en een aparte pagina zou alleen maar extra klikken kosten.
import { api, setToken } from "../api.js";
import { el, icon, brandMark, toast } from "../util.js";
import { t } from "../i18n.js";

export function renderLogin(root, onDone) {
  let mode = "login";                       // of "register"

  const email = el("input", { class: "field", type: "email", autocomplete: "email",
    placeholder: t("auth_email_ph"), "aria-label": t("auth_email") });
  const password = el("input", { class: "field", type: "password",
    autocomplete: "current-password", placeholder: t("auth_password_ph"), "aria-label": t("auth_password") });
  const errorBox = el("div", { class: "auth-error", style: "display:none" });
  const submitBtn = el("button", { class: "btn primary lg", style: "width:100%;justify-content:center" });
  const switchBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" });
  const title = el("h1", { class: "auth-title" });
  const sub = el("p", { class: "auth-sub" });

  const paint = () => {
    const isLogin = mode === "login";
    title.textContent = isLogin ? t("auth_login_title") : t("auth_register_title");
    sub.textContent = isLogin ? t("auth_login_sub") : t("auth_register_sub");
    submitBtn.replaceChildren(icon(isLogin ? "right" : "sparkle", "sm"),
      isLogin ? t("auth_login_btn") : t("auth_register_btn"));
    switchBtn.textContent = isLogin ? t("auth_to_register") : t("auth_to_login");
    password.autocomplete = isLogin ? "current-password" : "new-password";
    errorBox.style.display = "none";
  };

  const submit = async () => {
    const mail = email.value.trim();
    const pass = password.value;
    if (!mail || !pass) return;
    submitBtn.disabled = true;
    submitBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }));
    errorBox.style.display = "none";
    try {
      const res = mode === "login" ? await api.login(mail, pass) : await api.register(mail, pass);
      setToken(res.token);
      // Bij de allereerste registratie neemt de backend bestaande data over die
      // nog geen eigenaar had; dat is materiaal van vóór de accounts.
      if (res.adopted_documents) toast(t("auth_adopted", { n: res.adopted_documents }), "ok", 6000);
      onDone(res.user);
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.style.display = "";
      submitBtn.disabled = false;
      paint();
    }
  };

  submitBtn.addEventListener("click", submit);
  switchBtn.addEventListener("click", () => { mode = mode === "login" ? "register" : "login"; paint(); });
  for (const f of [email, password]) {
    f.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  }
  paint();

  root.append(el("div", { class: "auth-page" },
    el("div", { class: "auth-card" },
      el("div", { class: "auth-brand" }, brandMark()),
      title, sub,
      el("div", { style: "display:flex;flex-direction:column;gap:10px;margin-top:18px" }, email, password),
      errorBox,
      el("div", { style: "margin-top:16px" }, submitBtn),
      el("div", { style: "display:flex;justify-content:center;margin-top:10px" }, switchBtn),
      el("p", { class: "auth-fine" }, t("auth_privacy_note")),
    ),
  ));
  setTimeout(() => email.focus(), 50);
}
