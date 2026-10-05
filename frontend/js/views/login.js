// Inloggen / registreren / wachtwoord vergeten. Eén scherm met een schakelaar,
// want het verschil is klein en aparte pagina's zouden alleen klikken kosten.
import { api, setToken } from "../api.js";
import { el, icon, brandMark, toast } from "../util.js";
import { t } from "../i18n.js";

// Google's script wordt alleen geladen als jij een client-id hebt ingesteld, en
// alleen op dit scherm. Dat is de enige externe afhankelijkheid in de app; hij
// is inherent aan "inloggen met Google" en raakt de rest van de app niet.
const GOOGLE_SDK = "https://accounts.google.com/gsi/client";
let googleSdkPromise = null;
function loadGoogleSdk() {
  if (window.google?.accounts?.id) return Promise.resolve(window.google);
  if (!googleSdkPromise) {
    googleSdkPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = GOOGLE_SDK;
      s.async = true;
      s.onload = () => resolve(window.google);
      s.onerror = () => reject(new Error("google-sdk"));
      document.head.appendChild(s);
    });
  }
  return googleSdkPromise;
}

function passwordControl(input) {
  const toggle = el("button", {
    class: "password-toggle", type: "button",
    title: t("auth_show_password"), "aria-label": t("auth_show_password"),
    "aria-pressed": "false",
  }, icon("eye", "sm"));
  toggle.addEventListener("mousedown", (event) => event.preventDefault());
  toggle.addEventListener("click", () => {
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    const label = t(visible ? "auth_show_password" : "auth_hide_password");
    toggle.title = label;
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("aria-pressed", String(!visible));
    toggle.replaceChildren(icon(visible ? "eye" : "eye-off", "sm"));
    input.focus({ preventScroll: true });
  });
  return el("div", { class: "password-field" }, input, toggle);
}

export function renderLogin(root, onDone) {
  let mode = "login";                       // "login" | "register" | "forgot"
  let passwordResetReady = false;
  let emailRegistrationReady = false;

  const email = el("input", { class: "field", type: "email", autocomplete: "email",
    placeholder: t("auth_email_ph"), "aria-label": t("auth_email") });
  const password = el("input", { class: "field", type: "password",
    autocomplete: "current-password", placeholder: t("auth_password_ph"), "aria-label": t("auth_password") });
  const passwordRow = passwordControl(password);
  const errorBox = el("div", { class: "auth-error", style: "display:none" });
  const noticeBox = el("div", { class: "auth-notice", style: "display:none" });
  const submitBtn = el("button", { class: "btn primary lg", style: "width:100%;justify-content:center" });
  const switchBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" });
  const forgotBtn = el("button", { class: "btn ghost", style: "font-size:12.5px" }, t("auth_forgot"));
  const googleSlot = el("div", { style: "display:none" });
  const title = el("h1", { class: "auth-title" });
  const sub = el("p", { class: "auth-sub" });

  const paint = () => {
    const isLogin = mode === "login", isForgot = mode === "forgot";
    title.textContent = isForgot ? t("auth_forgot_title")
      : isLogin ? t("auth_login_title") : t("auth_register_title");
    sub.textContent = isForgot ? t("auth_forgot_sub")
      : isLogin ? t("auth_login_sub") : t("auth_register_sub");
    const buttonLabel = isForgot ? t("auth_forgot_btn") : isLogin ? t("auth_login_btn") : t("auth_register_btn");
    submitBtn.replaceChildren(
      ...(isForgot || isLogin ? [icon(isForgot ? "send" : "right", "sm")] : []),
      buttonLabel,
    );
    switchBtn.textContent = isForgot ? t("auth_back_to_login")
      : isLogin ? t("auth_to_register") : t("auth_to_login");
    passwordRow.style.display = isForgot ? "none" : "";
    forgotBtn.style.display = isLogin && passwordResetReady ? "" : "none";
    switchBtn.style.display = isForgot || emailRegistrationReady ? "" : "none";
    googleSlot.style.display = isForgot ? "none" : googleSlot.dataset.ready ? "" : "none";
    password.autocomplete = isLogin ? "current-password" : "new-password";
    errorBox.style.display = "none";
    noticeBox.style.display = "none";
  };

  const busy = (on) => {
    submitBtn.disabled = on;
    if (on) submitBtn.replaceChildren(el("span", { class: "spinner", style: "width:15px;height:15px;border-width:2px" }));
    else paint();
  };

  const finish = (res) => {
    setToken(res.token);
    // Bij de allereerste registratie neemt de backend bestaande data over die
    // nog geen eigenaar had; dat is materiaal van vóór de accounts.
    if (res.adopted_documents) toast(t("auth_adopted", { n: res.adopted_documents }), "ok", 6000);
    onDone(res.user);
  };

  const submit = async () => {
    const mail = email.value.trim();
    const pass = password.value;
    if (!mail || (mode !== "forgot" && !pass)) return;
    busy(true);
    try {
      if (mode === "forgot") {
        await api.forgotPassword(mail);
        // Bewust dezelfde melding, of het adres nu bestaat of niet.
        noticeBox.textContent = t("auth_forgot_sent");
        busy(false);
        noticeBox.style.display = "";
        return;
      }
      if (mode === "login") {
        finish(await api.login(mail, pass));
      } else {
        await api.register(mail, pass);
        password.value = "";
        busy(false);
        noticeBox.textContent = t("auth_register_sent");
        noticeBox.style.display = "";
      }
    } catch (err) {
      busy(false);
      errorBox.textContent = err.message;
      errorBox.style.display = "";
    }
  };

  submitBtn.addEventListener("click", submit);
  switchBtn.addEventListener("click", () => {
    mode = mode === "login" ? "register" : "login";
    paint();
  });
  forgotBtn.addEventListener("click", () => { mode = "forgot"; paint(); });
  for (const f of [email, password]) {
    f.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  }
  paint();

  root.append(el("div", { class: "auth-page" },
    el("div", { class: "auth-card" },
      el("div", { class: "auth-brand" }, brandMark()),
      title, sub,
      el("div", { style: "display:flex;flex-direction:column;gap:10px;margin-top:18px" }, email, passwordRow),
      errorBox, noticeBox,
      el("div", { style: "margin-top:16px" }, submitBtn),
      googleSlot,
      el("div", { class: "auth-links" }, switchBtn, forgotBtn),
      el("a", {
        class: "btn ghost", href: "#/privacy",
        style: "margin-top:8px;font-size:12px;align-self:center",
      }, t("privacy_link")),
    ),
  ));
  setTimeout(() => email.focus(), 50);

  // Google-knop alleen tonen als de server een client-id heeft. Lukt het laden
  // niet (geen internet, script geblokkeerd), dan blijft inloggen met e-mail
  // gewoon werken — daarom faalt dit stil.
  api.authConfig().then(async (cfg) => {
    passwordResetReady = Boolean(cfg?.password_reset);
    emailRegistrationReady = Boolean(cfg?.email_registration);
    paint();
    if (!cfg?.google_client_id) return;
    try {
      const google = await loadGoogleSdk();
      google.accounts.id.initialize({
        client_id: cfg.google_client_id,
        callback: async ({ credential }) => {
          busy(true);
          try { finish(await api.loginWithGoogle(credential)); }
          catch (err) { busy(false); errorBox.textContent = err.message; errorBox.style.display = ""; }
        },
      });
      const holder = el("div", {});
      googleSlot.replaceChildren(el("div", { class: "auth-divider" }, el("span", {}, t("auth_or"))), holder);
      googleSlot.dataset.ready = "1";
      googleSlot.style.display = mode === "forgot" ? "none" : "";
      google.accounts.id.renderButton(holder, {
        theme: "outline", size: "large", width: 320, text: "continue_with",
      });
    } catch { /* zonder Google blijft e-mail gewoon werken */ }
  }).catch(() => {});
}

// Eigen scherm voor de herstellink uit de mail (#/reset/<token>).
export function renderReset(root, token, onDone) {
  const password = el("input", { class: "field", type: "password", autocomplete: "new-password",
    placeholder: t("auth_password_ph"), "aria-label": t("auth_new_password") });
  const passwordRow = passwordControl(password);
  const errorBox = el("div", { class: "auth-error", style: "display:none" });
  const btn = el("button", { class: "btn primary lg", style: "width:100%;justify-content:center" },
    icon("check", "sm"), t("auth_reset_btn"));

  const submit = async () => {
    if (!password.value) return;
    btn.disabled = true;
    errorBox.style.display = "none";
    try {
      await api.resetPassword(token, password.value);
      toast(t("auth_reset_done"), "ok", 5000);
      onDone();
    } catch (err) {
      btn.disabled = false;
      errorBox.textContent = err.message;
      errorBox.style.display = "";
    }
  };
  btn.addEventListener("click", submit);
  password.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

  root.append(el("div", { class: "auth-page" },
    el("div", { class: "auth-card" },
      el("div", { class: "auth-brand" }, brandMark()),
      el("h1", { class: "auth-title" }, t("auth_reset_title")),
      el("p", { class: "auth-sub" }, t("auth_reset_sub")),
      el("div", { style: "margin-top:18px" }, passwordRow),
      errorBox,
      el("div", { style: "margin-top:16px" }, btn),
    ),
  ));
  setTimeout(() => password.focus(), 50);
}

// De link uit de verificatiemail activeert het account en logt meteen in.
export function renderEmailVerification(root, token, onDone) {
  const status = el("p", { class: "auth-sub" }, t("auth_verify_busy"));
  const errorBox = el("div", { class: "auth-error", style: "display:none" });
  const back = el("button", {
    class: "btn ghost", style: "display:none;margin:12px auto 0",
    onclick: () => { location.hash = "#/"; },
  }, t("auth_back_to_login"));

  root.append(el("div", { class: "auth-page" },
    el("div", { class: "auth-card" },
      el("div", { class: "auth-brand" }, brandMark()),
      el("h1", { class: "auth-title" }, t("auth_verify_title")),
      status,
      el("div", { class: "spinner", style: "margin:20px auto" }),
      errorBox,
      back,
    ),
  ));

  api.verifyEmail(token).then((res) => {
    setToken(res.token);
    toast(t("auth_verify_done"), "ok", 5000);
    onDone(res.user);
  }).catch((err) => {
    root.querySelector(".spinner")?.remove();
    status.textContent = t("auth_verify_failed");
    errorBox.textContent = err.message;
    errorBox.style.display = "";
    back.style.display = "flex";
  });
}
