/* NaturalSketch builder UI: home (studio, method, archive) and the project workspace.
   Look: a classic drafting-studio / editorial style. Motion: GSAP ScrollTrigger drives both the
   page and the Three.js scene in scene3d.js. Everything still works if GSAP or WebGL is missing. */
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const app = $("#app");

const esc = (text) =>
  String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Generated apps are served from a separate origin so their code is isolated from the builder.
// The server reports the real one in /api/status (set at startup, before the first render).
let PREVIEW = `${location.protocol}//127.0.0.1:${location.port || 80}`;

const icons = () => window.lucide && window.lucide.createIcons();
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const motionOk = () => Boolean(window.gsap && window.ScrollTrigger) && !reducedMotion();
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/session") {
    clearTimeout(ui.poll);
    ui.user = null;
    ui.token = null;
    renderAccount();
    openAuth("signin", "Please sign in to continue.");
    throw new Error("Sign in to continue.");
  }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

// ------------------------------------------------------------------ accounts (Supabase Auth)

const SUPABASE_JS = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.js";
const authState = { enabled: false, client: null };

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error("Couldn't load the sign-in library. Check your connection."));
    document.head.appendChild(s);
  });
}

async function initAuth(config) {
  if (!config) {
    ui.user = { local: true, name: "You" }; // local single-user mode: no accounts
    return;
  }
  authState.enabled = true;
  await loadScript(SUPABASE_JS);
  authState.client = window.supabase.createClient(config.url, config.anon_key, {
    auth: { flowType: "pkce", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });
  authState.client.auth.onAuthStateChange((event, session) => {
    // Don't await Supabase calls in here; only our own API.
    if (event === "PASSWORD_RECOVERY") setTimeout(() => openAuth("recover"), 0);
    if (event === "SIGNED_OUT") {
      ui.user = null;
      ui.token = null;
      renderAccount();
    } else if (session && session.access_token !== ui.token) {
      setTimeout(() => syncSession(session), 0);
    }
  });
  const { data } = await authState.client.auth.getSession();
  if (data.session) await syncSession(data.session);
}

// Hand the access token to the server, which keeps it in an HttpOnly cookie for this domain.
// Supabase's sign-in event and our own startup check can both call this for the same token;
// the second caller waits for the first one's request instead of returning before it finishes.
function syncSession(session) {
  if (!session) return Promise.resolve();
  if (session.access_token === ui.token) return ui.syncing || Promise.resolve();
  ui.token = session.access_token;
  const wasSignedIn = signedIn();
  const run = (async () => {
    try {
      const result = await api("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ access_token: session.access_token }) });
      ui.user = result.user;
      ui.usage = result.usage;
      ui.key = result.key || null;
    } catch (e) {
      ui.user = null;
      ui.token = null;
    }
    renderAccount();
    if (signedIn()) {
      const dialog = $("#auth");
      if (dialog && !dialog.hidden && (dialog.dataset.mode === "signin" || dialog.dataset.mode === "signup")) closeAuth(); // a sign-in prompt opened while this was in flight
      if (!wasSignedIn && !ui.booting && $("#project-list")) loadProjects();
    }
  })();
  ui.syncing = run;
  run.finally(() => {
    if (ui.syncing === run) ui.syncing = null;
  });
  return run;
}

async function signOut() {
  closeMenus();
  try {
    if (authState.client) await authState.client.auth.signOut();
    await fetch("/api/session", { method: "DELETE" });
  } catch (e) {
    /* signed out locally either way */
  }
  ui.user = null;
  ui.token = null;
  ui.usage = null;
  ui.key = null;
  if (location.hash.startsWith("#/p/")) location.hash = "#/";
  else route();
}

const signedIn = () => Boolean(ui.user);

function usageText() {
  const u = ui.usage;
  if (!u || u.limit == null) return "Unlimited builds";
  const left = Math.max(0, u.limit - u.used);
  return `${left} of ${u.limit} builds left today`;
}

async function refreshUsage() {
  if (!authState.enabled || !signedIn()) return;
  try {
    const result = await api("/api/me");
    ui.usage = result.usage;
    ui.key = result.key || null;
    renderAccount();
  } catch (e) {
    /* shown on the next action */
  }
}

// The line under the Build button: which models are used, and a visible way to add your own key.
function keyNote() {
  const free = "Free models by default (Gemini, Groq, OpenRouter): if one is busy, the next takes over.";
  if (!authState.enabled || !signedIn()) return esc(free);
  if (ui.key) {
    return `Using your own key (${esc(ui.key.provider)} · ${esc(ui.key.model)}) first, free models as fallback. <button type="button" class="link-btn" data-own-key>Change key</button>`;
  }
  return `${esc(free)} <button type="button" class="link-btn" data-own-key><i data-lucide="key-round"></i>Use your own AI key</button> for better results and 5× the daily builds.`;
}

function renderKeyNote() {
  const note = $("#key-note");
  if (!note) return;
  note.innerHTML = keyNote();
  const button = note.querySelector("[data-own-key]");
  if (button) button.addEventListener("click", openKeyDialog);
  icons();
}

function renderAccount() {
  renderKeyNote();
  $$(".account").forEach((host) => {
    if (ui.booting) {  // the server may be waking up: say so instead of showing nothing
      host.innerHTML = `<span class="account-wait"><i data-lucide="loader-2" class="spin"></i>Connecting…</span>`;
      return;
    }
    if (ui.serverDown) {
      host.innerHTML = `<button type="button" class="btn btn-ghost sm" data-retry><i data-lucide="refresh-cw"></i>Retry</button>`;
      host.querySelector("[data-retry]").addEventListener("click", () => location.reload());
      return;
    }
    if (!authState.enabled) {
      host.innerHTML = "";
      return;
    }
    if (!signedIn()) {
      host.innerHTML = `<button type="button" class="btn btn-ink sm" data-auth="signin"><i data-lucide="log-in"></i>Sign in</button>`;
    } else {
      const u = ui.user;
      const face = u.avatar
        ? `<img src="${esc(u.avatar)}" alt="" referrerpolicy="no-referrer">`
        : `<span>${esc((u.name || u.email || "?").charAt(0).toUpperCase())}</span>`;
      host.innerHTML = `
        <button type="button" class="account-chip" aria-haspopup="menu" aria-expanded="false">${face}<b class="hide-sm">${esc(u.name || u.email)}</b><i data-lucide="chevron-down"></i></button>
        <div class="account-menu" role="menu" hidden>
          <div class="account-who"><b>${esc(u.name || "")}</b><span>${esc(u.email)}</span></div>
          <div class="account-usage"><i data-lucide="gauge"></i>${esc(usageText())}</div>
          <a href="#/" role="menuitem" class="account-item"><i data-lucide="layout-grid"></i>My apps</a>
          <button type="button" role="menuitem" class="account-item" data-own-key><i data-lucide="key-round"></i>${ui.key ? `Your AI key <em>${esc(ui.key.provider)} ${esc(ui.key.hint)}</em>` : "Use your own AI key"}</button>
          <button type="button" role="menuitem" class="account-item" data-signout><i data-lucide="log-out"></i>Sign out</button>
        </div>`;
      const chip = host.querySelector(".account-chip");
      const menu = host.querySelector(".account-menu");
      chip.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = menu.hidden;
        closeMenus();
        menu.hidden = !open;
        chip.setAttribute("aria-expanded", String(open));
      });
      host.querySelector("[data-signout]").addEventListener("click", signOut);
      host.querySelector("[data-own-key]").addEventListener("click", openKeyDialog);
      host.querySelector('a[href="#/"]').addEventListener("click", () => {
        closeMenus();
        if (!location.hash.startsWith("#/p/")) setTimeout(() => scrollToSection("archive"), 50);
      });
    }
    host.querySelectorAll("[data-auth]").forEach((b) => b.addEventListener("click", () => openAuth(b.dataset.auth)));
  });
  icons();
}

// Your own AI key: used first in your builds, with the shared free models as the fallback.
const KEY_PROVIDERS = {
  gemini: { label: "Google Gemini", model: "gemini-3.5-flash", where: "aistudio.google.com/apikey" },
  groq: { label: "Groq", model: "openai/gpt-oss-120b", where: "console.groq.com/keys" },
  openrouter: { label: "OpenRouter (any model)", model: "openrouter/auto", where: "openrouter.ai/settings/keys" },
};

function openKeyDialog() {
  closeMenus();
  let dialog = $("#auth");
  if (!dialog) {
    dialog = document.createElement("div");
    dialog.id = "auth";
    dialog.className = "auth-backdrop";
    document.body.appendChild(dialog);
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) closeAuth();
    });
  }
  dialog.dataset.mode = "key";
  const current = ui.key;
  const provider = (current && current.provider) || "gemini";
  dialog.innerHTML = `
    <form class="auth-card" role="dialog" aria-modal="true" aria-labelledby="key-title" novalidate>
      <button type="button" class="icon-btn auth-close" aria-label="Close" data-close><i data-lucide="x"></i></button>
      <span class="brand-mark"><i data-lucide="key-round"></i></span>
      <h2 id="key-title">Your own AI key</h2>
      <p class="auth-sub">Builds use your key and model first, for better quality and 5× the daily builds. The shared free models stay as the fallback.</p>
      ${current ? `<div class="auth-msg ok">In use: ${esc(KEY_PROVIDERS[current.provider] ? KEY_PROVIDERS[current.provider].label : current.provider)} · ${esc(current.model)} · key ending ${esc(current.hint)}</div>` : ""}
      <label class="auth-field"><span>Provider</span>
        <select name="provider">${Object.entries(KEY_PROVIDERS).map(([k, v]) => `<option value="${k}" ${k === provider ? "selected" : ""}>${v.label}</option>`).join("")}</select>
      </label>
      <label class="auth-field"><span>Model</span>
        <input name="model" type="text" autocomplete="off" spellcheck="false" value="${esc(current ? current.model : KEY_PROVIDERS[provider].model)}">
      </label>
      <label class="auth-field"><span>API key</span>
        <input name="key" type="password" autocomplete="off" spellcheck="false" placeholder="${current ? "Paste a new key to replace it" : "Paste your key"}">
      </label>
      <p class="fine" id="key-where"></p>
      <div class="auth-msg" role="status" hidden></div>
      <button type="submit" class="btn btn-accent auth-submit">Check and save</button>
      ${current ? `<button type="button" class="btn btn-ghost" data-remove>Remove my key</button>` : ""}
      <p class="fine">Your key is encrypted before it is stored, is never shown again, and is only used for your own builds.</p>
    </form>`;
  dialog.hidden = false;
  document.body.classList.add("auth-open");
  icons();
  const form = dialog.querySelector("form");
  const msg = form.querySelector(".auth-msg[role=status]");
  const say = (text, tone = "bad") => {
    msg.hidden = !text;
    msg.className = `auth-msg ${tone}`;
    msg.textContent = text;
  };
  const where = () => {
    const p = KEY_PROVIDERS[form.provider.value];
    $("#key-where").textContent = `Get a key at ${p.where}. For OpenRouter, the model can be any id from their list.`;
  };
  where();
  form.provider.addEventListener("change", () => {
    form.model.value = KEY_PROVIDERS[form.provider.value].model;
    where();
  });
  form.querySelector("[data-close]").addEventListener("click", closeAuth);
  const submit = form.querySelector(".auth-submit");
  const apply = (result) => {
    ui.key = result.key;
    ui.usage = result.usage;
    renderAccount();
  };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.key.value.trim()) return say("Paste your API key.");
    say("");
    submit.disabled = true;
    submit.textContent = "Checking the key…";
    try {
      apply(await api("/api/key", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: form.provider.value, model: form.model.value.trim(), key: form.key.value.trim() }),
      }));
      closeAuth();
      flash("Your key works and is saved. Your builds will use it first.", "ok");
    } catch (error) {
      say(error.message);
    } finally {
      submit.disabled = false;
      submit.textContent = "Check and save";
    }
  });
  const remove = form.querySelector("[data-remove]");
  if (remove) {
    remove.addEventListener("click", async () => {
      try {
        apply(await api("/api/key", { method: "DELETE" }));
        closeAuth();
        flash("Your key was removed.", "ok");
      } catch (error) {
        say(error.message);
      }
    });
  }
}

function closeMenus() {
  $$(".account-menu").forEach((m) => (m.hidden = true));
  $$(".account-chip").forEach((c) => c.setAttribute("aria-expanded", "false"));
}
document.addEventListener("click", (e) => {
  if (!e.target.closest(".account")) closeMenus();
});

// The sign-in window: sign in, create account, forgot password, and set a new password.
function openAuth(mode = "signin", note = "") {
  if (!authState.enabled) return;
  closeMenus();
  let dialog = $("#auth");
  if (!dialog) {
    dialog = document.createElement("div");
    dialog.id = "auth";
    dialog.className = "auth-backdrop";
    document.body.appendChild(dialog);
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) closeAuth();
    });
  }
  const titles = {
    signin: ["Welcome back", "Sign in to your studio."],
    signup: ["Create your studio", "A free account keeps your apps, versions and chats."],
    forgot: ["Reset your password", "We'll email you a link to choose a new one."],
    recover: ["Choose a new password", "Enter it twice and you're back in."],
  };
  const [title, sub] = titles[mode];
  const google = mode === "signin" || mode === "signup";
  dialog.dataset.mode = mode;
  dialog.innerHTML = `
    <form class="auth-card" role="dialog" aria-modal="true" aria-labelledby="auth-title" novalidate>
      <button type="button" class="icon-btn auth-close" aria-label="Close" data-close><i data-lucide="x"></i></button>
      <span class="brand-mark"><i data-lucide="pen-tool"></i></span>
      <h2 id="auth-title">${title}</h2>
      <p class="auth-sub">${esc(note || sub)}</p>
      ${google ? `
        <button type="button" class="btn btn-ghost auth-google" data-google>
          <svg viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
          Continue with Google
        </button>
        <div class="auth-or"><span>or with email</span></div>` : ""}
      ${mode !== "recover" ? `<label class="auth-field"><span>Email</span><input name="email" type="email" autocomplete="email" required placeholder="you@example.com"></label>` : ""}
      ${mode === "signin" || mode === "signup" || mode === "recover" ? `
        <label class="auth-field"><span>${mode === "recover" ? "New password" : "Password"}</span>
          <input name="password" type="password" minlength="8" required autocomplete="${mode === "signin" ? "current-password" : "new-password"}" placeholder="${mode === "signin" ? "Your password" : "At least 8 characters"}">
        </label>` : ""}
      ${mode === "recover" ? `<label class="auth-field"><span>Repeat password</span><input name="password2" type="password" minlength="8" required autocomplete="new-password"></label>` : ""}
      ${mode === "signin" ? `<button type="button" class="auth-link auth-forgot" data-mode="forgot">Forgot password?</button>` : ""}
      <div class="auth-msg" role="status" hidden></div>
      <button type="submit" class="btn btn-accent auth-submit">${{ signin: "Sign in", signup: "Create account", forgot: "Send reset link", recover: "Save new password" }[mode]}</button>
      <p class="auth-switch">
        ${mode === "signin" ? `New here? <button type="button" class="auth-link" data-mode="signup">Create an account</button>` : ""}
        ${mode === "signup" ? `Already have an account? <button type="button" class="auth-link" data-mode="signin">Sign in</button>` : ""}
        ${mode === "forgot" ? `<button type="button" class="auth-link" data-mode="signin">Back to sign in</button>` : ""}
      </p>
    </form>`;
  dialog.hidden = false;
  document.body.classList.add("auth-open");
  icons();
  const form = dialog.querySelector("form");
  const msg = form.querySelector(".auth-msg");
  const say = (text, tone = "bad") => {
    msg.hidden = !text;
    msg.className = `auth-msg ${tone}`;
    msg.textContent = text;
  };
  const submit = form.querySelector(".auth-submit");
  const busyButton = (on) => {
    submit.disabled = on;
    submit.classList.toggle("loading", on);
  };
  const redirect = location.origin + location.pathname;

  form.querySelector("[data-close]").addEventListener("click", closeAuth);
  form.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => openAuth(b.dataset.mode)));
  const g = form.querySelector("[data-google]");
  if (g) {
    g.addEventListener("click", async () => {
      // Remember where we were; Google sends the browser back to the page root.
      try {
        sessionStorage.setItem("ns:return", location.hash);
      } catch (e) {
        /* fine without */
      }
      const { error } = await authState.client.auth.signInWithOAuth({ provider: "google", options: { redirectTo: redirect } });
      if (error) say(error.message);
    });
  }
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = form.email ? form.email.value.trim() : "";
    const password = form.password ? form.password.value : "";
    if (form.email && !/^\S+@\S+\.\S+$/.test(email)) return say("Enter a valid email address.");
    if (form.password && password.length < 8) return say("Passwords need at least 8 characters.");
    if (mode === "recover" && password !== form.password2.value) return say("The two passwords don't match.");
    say("");
    busyButton(true);
    try {
      const a = authState.client.auth;
      if (mode === "signin") {
        const { data, error } = await a.signInWithPassword({ email, password });
        if (error) throw error;
        await afterSignIn(data.session);
      } else if (mode === "signup") {
        const { data, error } = await a.signUp({ email, password, options: { emailRedirectTo: redirect + "?from=confirm" } });
        if (error) throw error;
        // Supabase doesn't reveal existing accounts: it answers with a user that has no identities.
        if (data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
          say("There's already an account with this email. Sign in instead, or use “Forgot password?”.");
        } else if (data.session) await afterSignIn(data.session);
        else say(`Almost there: we sent a confirmation link to ${email}. Open it in this browser to finish creating your account.`, "ok");
      } else if (mode === "forgot") {
        const { error } = await a.resetPasswordForEmail(email, { redirectTo: redirect + "?from=reset" });
        if (error) throw error;
        say(`If ${email} has an account, a reset link is on its way.`, "ok");
      } else if (mode === "recover") {
        const { data, error } = await a.updateUser({ password });
        if (error) throw error;
        const { data: s } = await a.getSession();
        await afterSignIn(s.session || (data && data.session));
        flash("Password updated.");
      }
    } catch (err) {
      say(friendlyAuthError(err));
    } finally {
      busyButton(false);
    }
  });
  const first = form.querySelector("input");
  if (first) setTimeout(() => first.focus(), 50);
}

function friendlyAuthError(err) {
  const m = (err && err.message) || String(err);
  if (/invalid login credentials/i.test(m)) return "That email and password don't match. Try again, or reset your password.";
  if (/email not confirmed/i.test(m)) return "Please confirm your email first: open the link we sent you.";
  if (/already registered|already exists/i.test(m)) return "There's already an account with this email. Sign in instead.";
  if (/rate limit|too many/i.test(m)) return "Too many attempts. Please wait a minute and try again.";
  return m;
}

function closeAuth() {
  const dialog = $("#auth");
  if (dialog) dialog.hidden = true;
  document.body.classList.remove("auth-open");
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#auth") && !$("#auth").hidden) closeAuth();
});

async function afterSignIn(session) {
  await syncSession(session);
  if (!signedIn()) throw new Error("Couldn't start your session. Please try again.");
  closeAuth();
  flash(`Signed in as ${ui.user.email}`, "ok");
  if ($("#home")) {
    loadProjects();
    if (ui.pendingBuild && $("#composer")) {
      ui.pendingBuild = false;
      $("#composer").requestSubmit();
    }
  } else route();
}

function timeAgo(seconds) {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

function flash(message, tone = "bad") {
  const el = document.createElement("div");
  el.className = `toast toast-${tone}`;
  el.setAttribute("role", "alert");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3600);
  setTimeout(() => el.remove(), 4000);
}

const TEMPLATES = [
  {
    label: "3D portfolio", icon: "box", kind: "react",
    prompt: "A portfolio for a creative developer and 3D artist, with an interactive 3D hero scene, smooth scroll animations and a bold, editorial feel.",
    features: "Interactive 3D hero that reacts to the mouse\nSelected work grid with hover effects\nProject detail page with images and a write-up\nAbout section with skills and experience timeline\nContact form with validation",
  },
  {
    label: "Product launch", icon: "rocket", kind: "react",
    prompt: "A launch website for a premium pair of wireless headphones, with a rotating 3D product, scroll-driven storytelling and pre-order.",
    features: "3D product that rotates as you scroll\nFeature highlights revealed with scroll animations\nTech specs comparison\nCustomer testimonials carousel\nPre-order form with colour selection",
  },
  {
    label: "Inventory app", icon: "database", kind: "fullstack", backend: "python",
    prompt: "An inventory manager for a small shop: track products, stock levels and suppliers, and record orders.",
    features: "Product list with search, category filter and low-stock highlight\nAdd, edit and delete products\nProduct detail page with stock history\nSuppliers list\nOrders: create an order and reduce stock\nDashboard with totals and low-stock alerts\nSign in",
  },
  {
    label: "Online store", icon: "shopping-bag",
    prompt: "A modern online store for handmade ceramics: mugs, bowls and vases from small studios.",
    features: "Product catalogue with search and category filters\nProduct detail page with reviews\nShopping cart with quantity changes and live totals\nCheckout form with order confirmation\nCustomer account with order history",
  },
  {
    label: "SaaS dashboard", icon: "layout-dashboard",
    prompt: "An analytics dashboard for a small subscription software company.",
    features: "KPI overview with revenue and signup charts\nCustomer list with search and plan filters\nCustomer detail page\nInvoices table with status filters and mark-as-paid\nTeam settings page\nLogin",
  },
  {
    label: "Restaurant", icon: "utensils",
    prompt: "A website for a cosy Italian restaurant in Bangalore with online table booking.",
    features: "Menu with categories and dietary tags\nTable reservation form with date, time and party size\nMy reservations page with cancel\nPhoto gallery\nContact, map and opening hours",
  },
  {
    label: "Task manager", icon: "kanban-square",
    prompt: "A lightweight team task manager, like a simpler Trello.",
    features: "Kanban board with To do / In progress / Done columns\nCreate, edit and delete tasks with priority and due date\nTask detail page with comments\nTeam members page\nLogin",
  },
];

const SUGGESTIONS = ["Switch to a dark theme", "Add a settings page", "Make the home page more visual", "Add a search bar to every list"];
const REACT_SUGGESTIONS = ["Add a 3D scene to the hero", "Add scroll animations", "Add a contact page", "Switch to a dark theme"];
const KINDS = {
  site: { label: "Website prototype", note: "Multi-page HTML, fastest to build" },
  react: { label: "React app", note: "React, 3D and motion, real project ZIP" },
  fullstack: { label: "Full-stack app", note: "React frontend + backend API" },
};
const BACKENDS = { python: "Python · FastAPI", node: "Node.js · Express", java: "Java" };
const FULLSTACK_SUGGESTIONS = ["Add a dashboard with totals", "Add search and filters to the list", "Add a form to create records", "Add a sign-in page"];

const STEPS = [
  { title: "Analyse", tag: "vision model · opencv", text: "A vision model reads your images: layout, components, text and style. Paper sketches are cleaned up with OpenCV first, lifting shadows and sharpening the lines, so nothing you drew is missed." },
  { title: "Plan", tag: "pages · navigation · theme · data", text: "The app gets a name, pages, navigation, a theme and realistic sample data. Every feature you listed is mapped to the page that will carry it." },
  { title: "Build", tag: "parallel pages · shared runtime", text: "Pages are written in parallel on a shared runtime: navigation shell, a persistent mock database, cart and wishlist state, mock login, toasts and dialogs. Buttons and forms actually work." },
  { title: "Iterate", tag: "chat · versions · restore", text: "Ask for changes in plain words. Pages are added, edited or removed; theme and name changes apply instantly. Every change becomes a version you can open and restore." },
  { title: "Export", tag: "zip · plain html", text: "Download a ZIP of plain HTML that runs anywhere: Netlify Drop, GitHub Pages, Vercel or any static host. Data and logins live in the browser, so no backend is needed." },
];

const CHAIN_LABELS = { VISION: "Image analysis", PLAN: "Planning", CODE: "Page code", CHAT: "Chat edits" };

const ui = { files: [], status: null, project: null, page: "index", viewVersion: null, device: "desktop", showCode: false, poll: null, error: null, loadedSrc: null, kind: "site", backend: "python", key: null };

function setKind(kind) {
  ui.kind = KINDS[kind] ? kind : "site";
  $$("[data-kind]").forEach((b) => {
    b.classList.toggle("on", b.dataset.kind === ui.kind);
    b.setAttribute("aria-checked", String(b.dataset.kind === ui.kind));
  });
  const pick = $("#backend-pick");
  if (pick) pick.hidden = ui.kind !== "fullstack";
  refreshTriggers();
}

function setBackend(language) {
  ui.backend = BACKENDS[language] ? language : "python";
  $$("[data-backend]").forEach((b) => {
    b.classList.toggle("on", b.dataset.backend === ui.backend);
    b.setAttribute("aria-checked", String(b.dataset.backend === ui.backend));
  });
}

// React and full-stack projects share the React preview, file browser and auto-fix.
const isReact = () => Boolean(ui.project && (ui.project.kind === "react" || ui.project.kind === "fullstack"));

// Where the current page of the viewed version is previewed. React projects are one app with
// routes after the #; HTML sites have a file per page.
function previewSrc(page = ui.page) {
  const base = `${PREVIEW}/preview/${ui.project.id}/${ui.viewVersion}`;
  return isReact() ? `${base}/index.html#/${page === "index" ? "" : page}` : `${base}/${page}.html`;
}

// The code view for React projects: every file of the version, with a file list.
function renderFileBrowser() {
  const p = ui.project;
  const key = `files:${p.id}:${ui.viewVersion}`;
  if (ui.loadedSrc === key) return;
  ui.loadedSrc = key;
  const stage = $("#stage");
  stage.innerHTML = `
    <div class="code-view files">
      <div class="code-head"><span id="file-name">Loading files…</span><button id="copy" class="btn btn-ghost sm dark">Copy</button></div>
      <div class="code-body"><nav class="file-list" id="file-list" aria-label="Project files"></nav><pre><code id="source"></code></pre></div>
    </div>`;
  fetch(`${PREVIEW}/preview/${p.id}/${ui.viewVersion}/__files.json`)
    .then((r) => r.json())
    .then((files) => {
      if (ui.loadedSrc !== key) return;
      const paths = Object.keys(files).sort((a, b) => {
        const depth = (s) => (s.startsWith("src/") ? 0 : 1);
        return depth(a) - depth(b) || a.localeCompare(b);
      });
      const list = $("#file-list");
      list.innerHTML = paths.map((path) => `<button type="button" data-file="${esc(path)}">${esc(path)}</button>`).join("");
      const show = (path) => {
        $("#file-name").textContent = `${path} · v${ui.viewVersion} · ${paths.length} files`;
        $("#source").textContent = files[path];
        list.querySelectorAll("[data-file]").forEach((b) => b.classList.toggle("on", b.dataset.file === path));
        $("#copy").textContent = "Copy";
        $("#copy").onclick = () => navigator.clipboard.writeText(files[path]).then(() => ($("#copy").textContent = "Copied"));
      };
      list.querySelectorAll("[data-file]").forEach((b) => b.addEventListener("click", () => show(b.dataset.file)));
      show(paths.find((x) => x === "src/App.jsx") || paths[0]);
    })
    .catch((e) => {
      $("#file-name").textContent = `Couldn't load the files: ${e.message}`;
    });
}

// Shared with scene3d.js; GSAP tweens these numbers as you scroll.
const sceneState = { x: 2.55, y: 0, scale: 1, rotX: 0.18, rotY: -0.42, spread: 0, fan: 0, active: 0, morph: 0, pencil: 0, fade: 1 };
const SCENE_START = { ...sceneState };

// ------------------------------------------------------------------ routing

// The page draws at once; anything that needs the server waits for this. On Render's free
// plan the server sleeps when idle and can take up to a minute to wake.
let markReady;
ui.booting = true;
ui.ready = new Promise((resolve) => (markReady = resolve));

function wakingNote(host, text) {
  // Only mention the wait if it's actually slow.
  const timer = setTimeout(() => {
    if (ui.booting && host.isConnected) {
      host.innerHTML = `<div class="waking"><i data-lucide="loader-2" class="spin"></i><p>${text}</p><span>The server is waking up. On the free plan this can take up to a minute the first time.</span></div>`;
      icons();
    }
  }, 1500);
  ui.ready.then(() => clearTimeout(timer));
}

function route() {
  clearTimeout(ui.poll);
  closeMenus();
  teardownHome();
  const match = location.hash.match(/^#\/p\/([a-f0-9]+)/);
  if (match && ui.booting) {
    const hash = location.hash;
    document.body.className = "mode-ws";
    app.innerHTML = `<div class="fullpage-msg" id="opening"></div>`;
    wakingNote($("#opening"), "Opening your app…");
    ui.ready.then(() => location.hash === hash && route());
    return;
  }
  if (match && authState.enabled && !signedIn()) {
    renderHome();
    openAuth("signin", "Sign in to open this app.");
  } else if (match) openProject(match[1]);
  else renderHome();
}
window.addEventListener("hashchange", route);

// Paste images straight into the composer (registered once, works whenever the composer is on screen).
document.addEventListener("paste", (e) => {
  if ($("#composer") && e.clipboardData && e.clipboardData.files.length) addFiles([...e.clipboardData.files]);
});

// ------------------------------------------------------------------ home

async function renderHome() {
  ui.project = null;
  document.body.className = "mode-home";
  document.title = "NaturalSketch · sketch it, describe it, ship it";
  window.scrollTo(0, 0);
  app.innerHTML = `
    <canvas id="scene" class="scene" aria-hidden="true"></canvas>
    <div class="home" id="home">
      <header class="masthead">
        <div class="masthead-rule"><span>Vol. I</span><span class="hide-sm">An app studio for drawings</span><span id="today"></span></div>
        <div class="masthead-main">
          <a href="#/" class="brand"><span class="brand-mark"><i data-lucide="pen-tool"></i></span><span class="brand-name">Natural<em>Sketch</em></span></a>
          <nav class="masthead-nav hide-sm">
            <button type="button" data-scroll="studio">Studio</button>
            <button type="button" data-scroll="method">Method</button>
            <button type="button" data-scroll="archive">Archive</button>
          </nav>
          <div class="account account-end"></div>
        </div>
      </header>

      <section class="hero" id="top">
        <div class="hero-copy">
          <p class="kicker reveal">Sketch it &nbsp;·&nbsp; Describe it &nbsp;·&nbsp; Ship it</p>
          <h1 class="hero-title">
            <span class="line"><span>From pencil</span></span>
            <span class="line"><span>line to a</span></span>
            <span class="line"><em>working app.</em></span>
          </h1>
          <p class="lede reveal">Upload wireframes, napkin sketches or screenshots, list the features you need, and get a working multi-page app you can keep refining by chat.</p>
          <div class="hero-actions reveal">
            <button type="button" class="btn btn-ink" data-scroll="studio"><i data-lucide="pencil-line"></i>Begin a drawing</button>
            <button type="button" class="btn btn-ghost" data-scroll="archive">Your apps <span id="app-count" class="count"></span></button>
          </div>
        </div>
        <div class="scroll-cue reveal" aria-hidden="true"><span>Scroll</span><i></i></div>
      </section>

      <section class="studio" id="studio">
        <div class="section-head reveal-up">
          <span class="folio">§ I</span>
          <h2>The Studio</h2>
          <p>Lay your drawings on the table, write the brief, list what it must do.</p>
        </div>
        <div class="studio-grid">
          <form id="composer" class="composer">
            <div class="tabs" aria-label="Start from a template">
              ${TEMPLATES.map((t, i) => `<button type="button" class="tab" data-template="${i}"><i data-lucide="${t.icon}"></i>${t.label}</button>`).join("")}
            </div>
            <div class="composer-body">
              <div class="field-label"><span>Fig. 0</span>What are we making?</div>
              <div class="kind-pick" role="radiogroup" aria-label="Project type">
                ${Object.entries(KINDS).map(([k, v]) => `<button type="button" role="radio" data-kind="${k}" class="kind ${k === ui.kind ? "on" : ""}" aria-checked="${k === ui.kind}"><b>${v.label}</b><span>${v.note}</span></button>`).join("")}
              </div>
              <div id="backend-pick" class="backend-pick" ${ui.kind === "fullstack" ? "" : "hidden"}>
                <span>Backend language</span>
                <div role="radiogroup" aria-label="Backend language">
                  ${Object.entries(BACKENDS).map(([k, label]) => `<button type="button" role="radio" data-backend="${k}" class="pill ${k === ui.backend ? "on" : ""}" aria-checked="${k === ui.backend}">${label}</button>`).join("")}
                </div>
              </div>
              <div class="field-label"><span>Fig. 1</span>Sketches &amp; screenshots</div>
              <label id="dropzone" class="dropzone">
                <span class="dz-icon"><i data-lucide="image-plus"></i></span>
                <span class="dz-main">Drop sketches or screenshots here, or <u>browse</u></span>
                <span class="dz-sub">Optional · up to 6 images, one per screen works best · you can also paste</span>
                <input id="file-input" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" multiple hidden>
              </label>
              <div id="thumbs" class="thumbs" hidden></div>
              <label class="field-label" for="prompt"><span>Fig. 2</span>The brief</label>
              <textarea id="prompt" class="ta ta-brief" rows="2" placeholder="Describe your app: what is it, who is it for?"></textarea>
              <label class="field-label" for="features"><span>Fig. 3</span>Features, one per line</label>
              <textarea id="features" class="ta ta-lined" rows="5" placeholder="e.g. Product catalogue with filters&#10;Shopping cart&#10;Checkout"></textarea>
              <label class="field-label" for="references"><span>Fig. 4</span>Reference websites <em>optional</em></label>
              <textarea id="references" class="ta ta-refs" rows="2" placeholder="Links to sites you like, one per line (up to 3)&#10;e.g. https://linear.app"></textarea>
              <p class="fine field-note">Their layout, colours and fonts are used as inspiration. Your site gets its own text and branding.</p>
            </div>
            <div class="composer-foot">
              <p class="fine" id="key-note">${keyNote()}</p>
              <button id="build" type="submit" class="btn btn-accent"><i data-lucide="sparkles"></i><span>Build app</span></button>
            </div>
          </form>
          <aside class="marginalia">
            <p class="note reveal-up"><span>Note a.</span> Paper sketches are cleaned automatically: shadows removed, lines thresholded.</p>
            <p class="note reveal-up"><span>Note b.</span> One image per screen gives the clearest result. Number them in the order you'd like the pages.</p>
            <p class="note reveal-up"><span>Note c.</span> No drawing? A good brief and a feature list are enough.</p>
          </aside>
        </div>
      </section>

      <section class="method" id="method">
        <div class="method-inner">
          <div class="method-head">
            <span class="folio">§ II</span>
            <h2>The Method</h2>
            <p>Five steps from the drawing table to a finished, working prototype.</p>
            <ol class="method-index" id="method-index">
              ${STEPS.map((s, i) => `<li data-step="${i}" class="${i === 0 ? "on" : ""}"><span>${ROMAN[i]}</span>${s.title}</li>`).join("")}
            </ol>
          </div>
          <div class="flipboard" id="flipboard">
            ${STEPS.map((s, i) => `
              <article class="plate" style="z-index:${i + 1}">
                <div class="plate-num">${ROMAN[i]}</div>
                <div class="plate-body">
                  <div class="plate-tag">${s.tag}</div>
                  <h3>${s.title}</h3>
                  <p>${s.text}</p>
                </div>
                <div class="plate-foot"><span>Plate ${i + 1} of ${STEPS.length}</span><span>NaturalSketch</span></div>
                <div class="plate-shade" aria-hidden="true"></div>
              </article>`).join("")}
          </div>
        </div>
      </section>

      <section class="archive" id="archive">
        <div class="section-head reveal-up">
          <span class="folio">§ III</span>
          <h2>The Archive</h2>
          <p>Every app you've built, newest first. Open one to keep refining it.</p>
        </div>
        <div id="project-list" class="archive-grid"><div class="muted">Loading…</div></div>
      </section>

      <footer class="colophon">
        <div class="colophon-grid">
          <div>
            <div class="brand brand-sm"><span class="brand-mark"><i data-lucide="pen-tool"></i></span><span class="brand-name">Natural<em>Sketch</em></span></div>
            <p class="fine">Generated apps run on a separate origin, so code written by the AI can't reach the builder or its API.</p>
            <p class="colophon-links"><a href="/privacy.html">Privacy Policy</a><a href="/terms.html">Terms of Service</a></p>
          </div>
          <div>
            <h4>Colophon · model chains</h4>
            <dl id="chains" class="chains"><dt>…</dt></dl>
          </div>
        </div>
      </footer>
    </div>`;
  $("#today").textContent = new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
  icons();
  renderAccount();
  bindComposer();
  renderThumbs();
  $$("[data-scroll]").forEach((b) => b.addEventListener("click", () => scrollToSection(b.dataset.scroll)));
  initHomeMotion();
  mountScene();
  loadStatus();
  loadProjects();
}

function scrollToSection(id) {
  const el = document.getElementById(id);
  if (!el) return;
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 10, behavior: reducedMotion() ? "auto" : "smooth" });
}

function mountScene() {
  const canvas = $("#scene");
  if (!canvas) return;
  const go = () => window.NSScene && $("#scene") === canvas && window.NSScene.mount(canvas, sceneState);
  if (window.NSScene) go();
  else window.addEventListener("nsscene:ready", go, { once: true });
}

function teardownHome() {
  if (window.ScrollTrigger) ScrollTrigger.getAll().forEach((t) => t.kill(true));
  if (window.gsap) gsap.killTweensOf(sceneState);
  Object.assign(sceneState, SCENE_START);
  if (window.NSScene) window.NSScene.unmount();
}

function initHomeMotion() {
  if (!motionOk()) {
    document.body.classList.add("no-motion");
    return;
  }
  gsap.registerPlugin(ScrollTrigger);

  // Opening titles
  gsap.from(".hero-title .line > *", { yPercent: 115, rotateX: -70, opacity: 0, duration: 1.3, ease: "expo.out", stagger: 0.12, delay: 0.1 });
  gsap.from(".hero .reveal", { y: 26, opacity: 0, duration: 1, ease: "power3.out", stagger: 0.1, delay: 0.55 });
  gsap.from(".masthead", { y: -30, opacity: 0, duration: 0.9, ease: "power3.out" });

  // Hero copy drifts up and away as you leave it
  gsap.to(".hero-copy", { yPercent: -18, opacity: 0.15, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });

  // Scene: the stack loosens as the studio arrives
  gsap.to(sceneState, {
    spread: 1, x: 3.1, y: -0.15, rotY: -0.28, rotX: 0.1, ease: "none",
    scrollTrigger: { trigger: "#studio", start: "top bottom", end: "top 20%", scrub: 1 },
  });

  // The composer swings up like a drawing board being raised
  gsap.fromTo("#composer",
    { rotateX: 22, y: 140, opacity: 0, transformPerspective: 1600, transformOrigin: "50% 100%" },
    { rotateX: 0, y: 0, opacity: 1, ease: "power2.out", scrollTrigger: { trigger: "#studio", start: "top 90%", end: "top 30%", scrub: 1 } });

  gsap.utils.toArray(".reveal-up").forEach((el) =>
    gsap.from(el, { y: 40, opacity: 0, duration: 1, ease: "power3.out", scrollTrigger: { trigger: el, start: "top 88%" } }));

  // The method: pinned flip-board, one plate per step; the sheets fan out and get "built"
  const plates = gsap.utils.toArray(".plate");
  const index = $$("#method-index li");
  const tl = gsap.timeline({
    defaults: { ease: "power2.inOut" },
    scrollTrigger: {
      trigger: "#method", start: "top top", end: `+=${plates.length * 85}%`, pin: true, scrub: 1, anticipatePin: 1,
      onUpdate: (self) => {
        const step = Math.min(plates.length - 1, Math.floor(self.progress * plates.length * 0.999 + 0.25));
        index.forEach((li, i) => li.classList.toggle("on", i === step));
      },
    },
  });
  tl.to(sceneState, { fan: 1, spread: 0.15, x: 3.7, y: -0.3, scale: 0.88, rotY: -0.22, rotX: 0.06, pencil: 1, duration: 0.8 }, 0);
  tl.to(sceneState, { morph: 1, duration: 0.6 }, 0.2);
  tl.to(sceneState, { active: plates.length - 1, duration: plates.length - 1, ease: "none" }, 0.5);
  plates.forEach((plate, i) => {
    if (!i) return;
    const at = i - 0.5;
    tl.fromTo(plate, { rotateX: 96 }, { rotateX: 0, duration: 1 }, at);
    tl.to(plates[i - 1], { scale: 0.94, y: -18, duration: 1 }, at);
    tl.to(plates[i - 1].querySelector(".plate-shade"), { opacity: 1, duration: 1 }, at);
  });
  tl.to({}, { duration: 0.5 });

  // The archive: the scene steps back and fades so the plates can be read
  gsap.to(sceneState, {
    fade: 0, y: 1.8, scale: 0.85, ease: "none",
    scrollTrigger: { trigger: "#archive", start: "top 95%", end: "top 35%", scrub: 1 },
  });
}

async function loadStatus() {
  await ui.ready; // the startup code fetches it once
  if (!ui.status) return;
  const chains = $("#chains");
  if (chains && ui.status.chains) {
    const cooling = ui.status.cooling_down || {};
    chains.innerHTML = Object.entries(ui.status.chains)
      .map(([task, models]) => `<dt>${esc(CHAIN_LABELS[task.toUpperCase()] || task)}</dt><dd>${models.map((m) => `<span class="${cooling[m] ? "cooling" : ""}" title="${cooling[m] ? `Cooling down, ${cooling[m]}s` : ""}">${esc(m)}</span>`).join("<b>→</b>")}</dd>`)
      .join("");
  }
}

async function loadProjects() {
  let host = $("#project-list");
  if (ui.booting) {
    if (host) wakingNote(host, "Loading your apps…");
    await ui.ready;
    host = $("#project-list");
    if (!host || !host.isConnected) return;
  }
  if (host && ui.serverDown) {
    host.innerHTML = `<div class="empty"><i data-lucide="cloud-off"></i><p>The server isn't answering right now. It may still be starting up.</p>
      <div class="empty-actions"><button type="button" class="btn btn-accent sm" data-retry>Try again</button></div></div>`;
    host.querySelector("[data-retry]").addEventListener("click", () => location.reload());
    icons();
    return;
  }
  if (host && authState.enabled && !signedIn()) {
    const count = $("#app-count");
    if (count) count.textContent = "";
    host.innerHTML = `<div class="empty"><i data-lucide="lock-keyhole"></i><p>Your apps are private to your account.</p>
      <div class="empty-actions"><button type="button" class="btn btn-accent sm" data-auth="signup">Create a free account</button><button type="button" class="btn btn-ghost sm" data-auth="signin">Sign in</button></div></div>`;
    host.querySelectorAll("[data-auth]").forEach((b) => b.addEventListener("click", () => openAuth(b.dataset.auth)));
    icons();
    refreshTriggers();
    return;
  }
  let list = [];
  try {
    list = await api("/api/projects");
  } catch (e) {
    if (host) host.innerHTML = `<div class="error-text">${esc(e.message)}</div>`;
    return;
  }
  if (!host || !host.isConnected) return;
  const count = $("#app-count");
  if (count) count.textContent = list.length ? String(list.length) : "";
  if (!list.length) {
    host.innerHTML = `<div class="empty"><i data-lucide="feather"></i><p>No apps yet. Build your first one in the studio above.</p></div>`;
    icons();
    refreshTriggers();
    return;
  }
  host.innerHTML = list
    .map((p, i) => {
      const thumb = p.kind === "react" && p.current
        ? `<div class="thumb-react"><i data-lucide="box"></i><span>React project</span></div>`
        : p.kind === "fullstack" && p.current
        ? `<div class="thumb-react"><i data-lucide="server"></i><span>Full-stack project</span></div>`
        : p.current
        ? `<iframe src="${PREVIEW}/preview/${p.id}/${p.current}/index.html" tabindex="-1" loading="lazy" title="" aria-hidden="true"></iframe>`
        : p.thumbnail
          ? `<img src="/api/projects/${p.id}/uploads/${p.thumbnail}" alt="" class="sketch-thumb">`
          : `<div class="thumb-empty"><i data-lucide="layout-template"></i></div>`;
      const tone = { ready: "ok", error: "bad" }[p.status] || "warn";
      return `
        <a href="#/p/${p.id}" class="card" data-tilt>
          <div class="card-mat"><div class="card-thumb">${thumb}</div></div>
          <div class="card-cap">
            <div class="card-no">Plate ${ROMAN[i] || i + 1}</div>
            <div class="card-name">${esc(p.name)}</div>
            <div class="card-meta">${timeAgo(p.updated)} · ${p.versions} version${p.versions === 1 ? "" : "s"}</div>
          </div>
          <span class="stamp stamp-${tone}">${esc(p.status)}</span>
          <span class="card-tools">
            ${p.current ? `<button type="button" data-export="${p.id}" data-version="${p.current}" title="Export ZIP" aria-label="Export ${esc(p.name)} as a ZIP" class="card-tool"><i data-lucide="download"></i></button>` : ""}
            <button type="button" data-delete="${p.id}" title="Delete" aria-label="Delete ${esc(p.name)}" class="card-tool card-del"><i data-lucide="trash-2"></i></button>
          </span>
        </a>`;
    })
    .join("");
  icons();
  host.querySelectorAll("[data-export]").forEach((button) =>
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      location.href = `/api/projects/${button.dataset.export}/download?version=${button.dataset.version}`;
    }),
  );
  host.querySelectorAll("[data-delete]").forEach((button) =>
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!confirm("Delete this app and all its versions? This cannot be undone.")) return;
      try {
        await api(`/api/projects/${button.dataset.delete}`, { method: "DELETE" });
        loadProjects();
      } catch (e) {
        flash(e.message);
      }
    }),
  );
  bindTilt(host);
  if (motionOk()) {
    gsap.set(".card", { opacity: 0, y: 60, rotateX: 18, transformPerspective: 1200 });
    ScrollTrigger.batch(".card", {
      start: "top 92%",
      onEnter: (batch) => gsap.to(batch, { opacity: 1, y: 0, rotateX: 0, duration: 1, ease: "power3.out", stagger: 0.08, clearProps: "transform" }),
    });
  }
  refreshTriggers();
}

function refreshTriggers() {
  if (motionOk()) requestAnimationFrame(() => ScrollTrigger.refresh());
}

// Cards lean toward the pointer, like a print lifted off the table.
function bindTilt(root) {
  if (reducedMotion() || !matchMedia("(hover: hover)").matches) return;
  root.querySelectorAll("[data-tilt]").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5;
      const y = (e.clientY - r.top) / r.height - 0.5;
      card.style.transform = `perspective(900px) rotateX(${(-y * 9).toFixed(2)}deg) rotateY(${(x * 11).toFixed(2)}deg) translateZ(14px)`;
      card.style.setProperty("--gx", `${(x + 0.5) * 100}%`);
      card.style.setProperty("--gy", `${(y + 0.5) * 100}%`);
    });
    card.addEventListener("pointerleave", () => (card.style.transform = ""));
  });
}

function addFiles(files) {
  for (const file of files) {
    if (!file.type.startsWith("image/")) continue;
    if (ui.files.length >= 6) {
      flash("Up to 6 images");
      break;
    }
    ui.files.push({ file, url: URL.createObjectURL(file) });
  }
  renderThumbs();
}

function bindComposer() {
  const input = $("#file-input");
  const zone = $("#dropzone");
  input.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((type) => zone.addEventListener(type, (e) => (e.preventDefault(), zone.classList.add("dragging"))));
  ["dragleave", "drop"].forEach((type) => zone.addEventListener(type, () => zone.classList.remove("dragging")));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    addFiles(e.dataTransfer.files);
  });

  $$("[data-kind]").forEach((button) => button.addEventListener("click", () => setKind(button.dataset.kind)));
  $$("[data-backend]").forEach((button) => button.addEventListener("click", () => setBackend(button.dataset.backend)));
  $$("[data-template]").forEach((button) =>
    button.addEventListener("click", () => {
      const t = TEMPLATES[button.dataset.template];
      $("#prompt").value = t.prompt;
      $("#features").value = t.features;
      setKind(t.kind || "site");
      if (t.backend) setBackend(t.backend);
      $$("[data-template]").forEach((b) => b.classList.toggle("on", b === button));
    }),
  );

  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = $("#prompt").value.trim();
    const references = $("#references").value.trim();
    if (!prompt && !ui.files.length && !references) {
      flash("Add a sketch, a reference site or a description first.");
      return;
    }
    if (ui.booting) {
      const b = $("#build");
      b.disabled = true;
      b.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Waking the server…</span>`;
      icons();
      await ui.ready;
      b.disabled = false;
      b.innerHTML = `<i data-lucide="sparkles"></i><span>Build app</span>`;
      icons();
    }
    if (authState.enabled && !signedIn()) {
      ui.pendingBuild = true; // build straight after signing in; the brief stays as typed
      openAuth("signup", "Create a free account to build your app. Your brief and sketches are kept.");
      return;
    }
    const button = $("#build");
    button.disabled = true;
    button.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Starting…</span>`;
    icons();
    try {
      const form = new FormData();
      form.append("prompt", prompt);
      form.append("features", $("#features").value);
      form.append("kind", ui.kind);
      form.append("backend", ui.backend);
      form.append("references", references);
      for (const { file } of ui.files) form.append("images", await shrinkImage(file));
      const { id } = await api("/api/projects", { method: "POST", body: form });
      ui.files = [];
      refreshUsage();
      location.hash = `#/p/${id}`;
    } catch (err) {
      flash(err.message);
      button.disabled = false;
      button.innerHTML = `<i data-lucide="sparkles"></i><span>Build app</span>`;
      icons();
    }
  });
}

// Big photos are scaled down in the browser first: uploads stay small and fast, and well under
// the size limit of the Vercel → Render proxy. 2000px is plenty for reading a sketch.
async function shrinkImage(file, max = 2000) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
    if (scale === 1 && file.size < 1.5 * 1024 * 1024) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // transparent PNGs become white, like paper
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
    return blob ? new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" }) : file;
  } catch (e) {
    return file;
  }
}

function renderThumbs() {
  const host = $("#thumbs");
  if (!host) return;
  host.hidden = !ui.files.length;
  host.innerHTML = ui.files
    .map(
      (f, i) => `
      <figure class="thumb" style="--r:${((i % 3) - 1) * 1.6}deg">
        <img src="${f.url}" alt="Upload ${i}">
        <figcaption>${i}</figcaption>
        <button type="button" data-remove="${i}" aria-label="Remove image ${i}"><i data-lucide="x"></i></button>
      </figure>`,
    )
    .join("");
  host.querySelectorAll("[data-remove]").forEach((b) =>
    b.addEventListener("click", (e) => {
      e.preventDefault();
      ui.files.splice(Number(b.dataset.remove), 1);
      renderThumbs();
    }),
  );
  icons();
  refreshTriggers();
}

// ------------------------------------------------------------------ project workspace

async function openProject(pid) {
  ui.page = "index";
  ui.viewVersion = null;
  ui.showCode = false;
  ui.error = null;
  ui.loadedSrc = null;
  document.body.className = "mode-ws";
  app.innerHTML = `
    <div class="ws">
      <header class="ws-bar">
        <a href="#/" class="icon-btn" title="All apps" aria-label="All apps"><i data-lucide="arrow-left"></i></a>
        <span class="brand-mark sm"><i data-lucide="pen-tool"></i></span>
        <div class="ws-title-wrap">
          <div id="title" class="ws-title"></div>
          <div id="state" class="ws-state"></div>
        </div>
        <div class="ws-actions">
          <label class="select-wrap" title="Versions"><i data-lucide="history"></i><select id="versions" aria-label="Version"></select></label>
          <a id="open-tab" target="_blank" rel="noopener" class="btn btn-ghost sm hide-sm"><i data-lucide="external-link"></i>Open</a>
          <a id="download" class="btn btn-accent sm" title="Download the code as a ZIP"><i data-lucide="download"></i>Export<span class="hide-sm">&nbsp;ZIP</span></a>
          <div class="account"></div>
        </div>
      </header>
      <div class="ws-body">
        <aside class="ws-side">
          <div id="timeline" class="timeline"></div>
          <div id="suggestions" class="suggestions"></div>
          <form id="chat" class="chat">
            <div class="chat-box">
              <textarea id="message" rows="3" placeholder="Ask for a change: “add a wishlist page”, “make the cards bigger”…"></textarea>
              <div class="chat-foot">
                <span class="hint">Enter to send · Shift+Enter for a new line</span>
                <button id="send" class="send" aria-label="Send"><i data-lucide="arrow-up"></i></button>
              </div>
            </div>
          </form>
        </aside>
        <main class="ws-main">
          <div class="toolbar">
            <div id="pages" class="page-tabs"></div>
            <div class="tools">
              ${[["desktop", "monitor"], ["tablet", "tablet"], ["mobile", "smartphone"]].map(([d, icon]) => `<button data-device="${d}" title="${d}" aria-label="${d} view" class="tool"><i data-lucide="${icon}"></i></button>`).join("")}
              <span class="tool-sep"></span>
              <button id="reset" title="Reset demo data" aria-label="Reset demo data" class="tool"><i data-lucide="rotate-ccw"></i></button>
              <button id="reload" title="Reload" aria-label="Reload" class="tool"><i data-lucide="refresh-cw"></i></button>
              <button id="code" title="View code" aria-label="View code" class="tool"><i data-lucide="code-2"></i></button>
            </div>
          </div>
          <div id="error-banner" class="hidden"></div>
          <div id="stage" class="stage"></div>
        </main>
      </div>
    </div>`;
  icons();
  renderAccount();
  if (window.gsap && !reducedMotion()) {
    gsap.from(".ws-bar", { y: -20, opacity: 0, duration: 0.6, ease: "power3.out" });
    gsap.from(".ws-side", { x: -30, opacity: 0, duration: 0.7, ease: "power3.out", delay: 0.05 });
  }
  bindWorkspace(pid);
  await refresh(pid);
}

function bindWorkspace(pid) {
  const message = $("#message");
  $("#chat").addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(pid, message.value);
  });
  message.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(pid, message.value);
    }
  });
  $("#versions").addEventListener("change", (e) => {
    ui.viewVersion = Number(e.target.value);
    renderAll();
  });
  $$("[data-device]").forEach((b) =>
    b.addEventListener("click", () => {
      ui.device = b.dataset.device;
      renderToolbar();
      renderStage();
    }),
  );
  $("#reload").addEventListener("click", () => {
    ui.loadedSrc = null;
    renderStage();
  });
  $("#reset").addEventListener("click", () => {
    const frame = $("#preview");
    if (frame) frame.contentWindow.postMessage({ type: "ns:reset" }, "*");
  });
  $("#code").addEventListener("click", () => {
    ui.showCode = !ui.showCode;
    ui.loadedSrc = null;
    renderToolbar();
    renderStage();
  });
}

async function refresh(pid) {
  clearTimeout(ui.poll);
  if (!location.hash.includes(pid)) return;
  let project;
  try {
    project = await api(`/api/projects/${pid}`);
  } catch (e) {
    if (authState.enabled && !signedIn()) return;
    app.innerHTML = `<div class="fullpage-msg"><p>${esc(e.message)}</p><a href="#/" class="btn btn-ghost"><i data-lucide="arrow-left"></i>Back to all apps</a></div>`;
    icons();
    return;
  }
  if (!location.hash.includes(pid)) return;
  const previous = ui.project;
  ui.project = project;
  if (!ui.viewVersion || (previous && previous.current !== project.current)) ui.viewVersion = project.current;
  const pages = currentPages();
  if (pages.length && !pages.includes(ui.page)) ui.page = "index";
  renderAll();
  if (project.running || ["queued", "building"].includes(project.status)) ui.poll = setTimeout(() => refresh(pid), 1500);
}

const busy = () => ui.project && (ui.project.running || ["queued", "building"].includes(ui.project.status));
const currentPages = () => ((ui.project.versions.find((v) => v.n === ui.viewVersion) || {}).pages || []);

function renderAll() {
  renderHeader();
  renderTimeline();
  renderToolbar();
  renderStage();
}

function renderHeader() {
  const p = ui.project;
  $("#title").textContent = p.name;
  document.title = `${p.name} · NaturalSketch`;
  $("#state").innerHTML = busy()
    ? `<span class="state-chip warn"><i data-lucide="loader-2" class="spin"></i>Building</span>`
    : p.status === "error"
      ? `<span class="state-chip bad">Build failed</span>`
      : p.current ? `<span class="state-chip ok">v${p.current} · ${p.versions.length} version${p.versions.length === 1 ? "" : "s"}</span>` : "";
  const versions = [...p.versions].reverse();
  $("#versions").innerHTML = versions.length
    ? versions.map((v) => `<option value="${v.n}" ${v.n === ui.viewVersion ? "selected" : ""}>v${v.n} · ${esc(v.message)}${v.n === p.current ? " (current)" : ""}</option>`).join("")
    : `<option>No versions yet</option>`;
  $("#versions").disabled = !versions.length;
  const ready = Boolean(ui.viewVersion);
  const download = $("#download");
  download.href = ready ? `/api/projects/${p.id}/download?version=${ui.viewVersion}` : "#";
  download.classList.toggle("disabled", !ready);
  $("#open-tab").href = ready ? previewSrc() : "#";
  $("#open-tab").classList.toggle("disabled", !ready);
  $("#send").disabled = busy();
  icons();
}

function renderTimeline() {
  const p = ui.project;
  const host = $("#timeline");
  const nearBottom = host.scrollHeight - host.scrollTop - host.clientHeight < 80;

  const request = `
    <div class="brief-card">
      <div class="brief-label">The brief</div>
      ${p.images.length ? `<div class="brief-images">${p.images.map((img, i) => `<a href="/api/projects/${p.id}/uploads/${img.file}" target="_blank" rel="noopener" style="--r:${((i % 3) - 1) * 2}deg"><img src="/api/projects/${p.id}/uploads/${img.file}" alt="Upload ${i}"><span>${i} · ${esc(img.kind)}</span></a>`).join("")}</div>` : ""}
      ${p.prompt ? `<p class="brief-prompt">${esc(p.prompt)}</p>` : ""}
      ${p.features.length ? `<ul class="brief-features">${p.features.map((f) => `<li><i data-lucide="check"></i>${esc(f)}</li>`).join("")}</ul>` : ""}
      ${p.kind !== "site" ? `<div class="brief-tags"><span>${esc(KINDS[p.kind] ? KINDS[p.kind].label : p.kind)}</span>${p.backend ? `<span>${esc(BACKENDS[p.backend] || p.backend)} backend</span>` : ""}</div>` : ""}
      ${(p.refs || []).length ? `<ul class="brief-refs">${p.refs.map((r) => `<li class="${r.error ? "bad" : ""}" title="${esc(r.error || "")}"><i data-lucide="${r.error ? "link-2-off" : "link"}"></i><a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.url.replace(/^https?:\/\//, "").replace(/\/$/, ""))}</a>${r.error ? `<em>couldn't be read</em>` : ""}</li>`).join("")}</ul>` : ""}
    </div>`;

  const items = [...p.chat.map((m) => ({ ...m, type: "chat" })), ...p.log.map((l) => ({ ...l, type: "log" }))].sort((a, b) => a.t - b.t);
  const logIcon = { step: "circle-dot", done: "check-circle-2", warn: "alert-triangle", error: "x-circle", info: "info" };
  const rows = items
    .map((item) => {
      if (item.type === "chat" && item.role === "user") {
        return `<div class="msg msg-user"><div>${esc(item.content)}</div></div>`;
      }
      if (item.type === "chat") {
        return `<div class="msg msg-ai"><span class="seal">NS</span><div>${esc(item.content)}</div></div>`;
      }
      const kind = logIcon[item.kind] ? item.kind : "info";
      return `<div class="log log-${kind}">
        <i data-lucide="${logIcon[kind]}"></i>
        <div><span>${esc(item.msg)}</span>${item.model ? `<code class="model">${esc(item.model)}</code>` : ""}</div></div>`;
    })
    .join("");

  const working = busy() ? `<div class="working"><i data-lucide="loader-2" class="spin"></i>Working…</div>` : "";
  const retry = p.status === "error" && !busy()
    ? `<div class="retry-card"><div>${esc(p.error || "The build failed.")}</div><button id="retry" class="btn btn-bad sm">Retry build</button></div>`
    : "";
  host.innerHTML = request + rows + working + retry;
  icons();
  const retryButton = $("#retry");
  if (retryButton) retryButton.addEventListener("click", () => rebuild(p.id));
  if (nearBottom || busy()) host.scrollTop = host.scrollHeight;

  const suggestions = $("#suggestions");
  suggestions.innerHTML = p.current && !busy()
    ? (p.kind === "fullstack" ? FULLSTACK_SUGGESTIONS : isReact() ? REACT_SUGGESTIONS : SUGGESTIONS).map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("")
    : "";
  suggestions.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => sendMessage(p.id, b.textContent)));
}

function renderToolbar() {
  const p = ui.project;
  const pages = currentPages();
  $("#pages").innerHTML = pages
    .map((slug) => `<button data-page="${esc(slug)}" class="page-tab ${slug === ui.page ? "on" : ""}">${esc(slug === "index" ? "home" : slug)}</button>`)
    .join("");
  $("#pages").querySelectorAll("[data-page]").forEach((b) =>
    b.addEventListener("click", () => {
      ui.page = b.dataset.page;
      if (!isReact()) ui.loadedSrc = null; // React: same app, just another route
      renderToolbar();
      renderStage();
      renderHeader();
    }),
  );
  $$("[data-device]").forEach((b) => b.classList.toggle("on", b.dataset.device === ui.device && !ui.showCode));
  $("#code").classList.toggle("on", ui.showCode);
  if (p) $("#open-tab").href = ui.viewVersion ? previewSrc() : "#";
}

const DRAWING = `
  <svg viewBox="0 0 240 300" class="drawing" aria-hidden="true">
    <rect x="16" y="16" width="208" height="26" rx="3"/>
    <circle cx="32" cy="29" r="6"/>
    <path d="M160 29h48M120 29h28"/>
    <rect x="16" y="56" width="208" height="92" rx="3"/>
    <path d="M16 56l208 92M224 56L16 148"/>
    <path d="M16 170h130M16 186h96"/>
    <rect x="16" y="202" width="70" height="20" rx="10"/>
    <rect x="16" y="236" width="62" height="48" rx="3"/><rect x="89" y="236" width="62" height="48" rx="3"/><rect x="162" y="236" width="62" height="48" rx="3"/>
  </svg>`;

function renderStage() {
  const p = ui.project;
  const stage = $("#stage");
  renderErrorBanner();

  if (!ui.viewVersion) {
    ui.loadedSrc = null;
    const latest = [...p.log].reverse().find((l) => l.kind !== "warn");
    const failed = p.status === "error" && !busy();
    stage.innerHTML = `
      <div class="drafting ${failed ? "failed" : ""}">
        <div class="draft-stack">
          <div class="draft-sheet ${failed ? "" : "scanning"}">
            ${p.images.length ? `<img src="/api/projects/${p.id}/uploads/${p.images[0].file}" alt="Your sketch">` : failed ? `<i data-lucide="alert-triangle" class="draft-icon"></i>` : DRAWING}
          </div>
        </div>
        <div class="draft-title">${failed ? "The build didn't finish" : "Building your app…"}</div>
        <div class="draft-msg">${esc(latest ? latest.msg : "Starting up")}</div>
        ${failed ? "" : `<div class="draft-note">Free-tier models can take a few minutes, especially when they're busy.</div>`}
      </div>`;
    icons();
    return;
  }

  const src = previewSrc();
  if (ui.showCode && isReact()) {
    renderFileBrowser();
    return;
  }
  if (ui.showCode) {
    if (ui.loadedSrc === "code:" + src) return;
    ui.loadedSrc = "code:" + src;
    stage.innerHTML = `<div class="code-view"><div class="code-head"><span>${esc(ui.page)}.html · v${ui.viewVersion}</span><button id="copy" class="btn btn-ghost sm dark">Copy</button></div><pre><code id="source">Loading…</code></pre></div>`;
    fetch(src)
      .then((r) => r.text())
      .then((text) => {
        const source = $("#source");
        if (!source) return;
        source.textContent = text;
        $("#copy").onclick = () => navigator.clipboard.writeText(text).then(() => ($("#copy").textContent = "Copied"));
      })
      .catch((e) => {
        const source = $("#source");
        if (source) source.textContent = `Couldn't load the source: ${e.message}`;
      });
    return;
  }

  const width = { desktop: "100%", tablet: "820px", mobile: "390px" }[ui.device];
  const existing = $("#preview");
  if (existing && (ui.loadedSrc === src || (isReact() && ui.loadedSrc && ui.loadedSrc.split("#")[0] === src.split("#")[0]))) {
    const frame = existing.closest(".device-frame");
    frame.style.width = width;
    frame.dataset.device = ui.device;
    if (ui.loadedSrc !== src) {
      existing.src = src; // React: same app, another route (a fragment change, no reload)
      ui.loadedSrc = src;
    }
    return;
  }
  ui.loadedSrc = src;
  const viewingOld = ui.viewVersion !== p.current;
  stage.innerHTML = `
    <div class="device-frame" data-device="${ui.device}" style="width:${width}">
      ${viewingOld ? `<div class="old-banner"><i data-lucide="history"></i><span>Viewing version ${ui.viewVersion}</span><button id="restore" class="btn btn-ink sm">Restore this version</button></div>` : ""}
      <div class="device-glass"><iframe id="preview" src="${src}" title="App preview"></iframe></div>
    </div>`;
  icons();
  const restoreButton = $("#restore");
  if (restoreButton) restoreButton.addEventListener("click", () => restore(p.id, ui.viewVersion));
}

function renderErrorBanner() {
  const banner = $("#error-banner");
  if (!ui.error || ui.showCode) {
    banner.className = "hidden";
    banner.innerHTML = "";
    return;
  }
  banner.className = "error-banner";
  banner.innerHTML = `<i data-lucide="bug"></i><span class="error-text-line">Error on <b>${esc(ui.error.page)}</b>: ${esc(ui.error.message)}</span>
    <button id="fix" class="btn btn-bad sm">Fix with AI</button>
    <button id="dismiss" class="icon-btn sm" aria-label="Dismiss"><i data-lucide="x"></i></button>`;
  icons();
  $("#fix").onclick = () => {
    const { page, message } = ui.error;
    ui.error = null;
    sendMessage(ui.project.id, `Fix this JavaScript error on the ${page} page: ${message}`);
  };
  $("#dismiss").onclick = () => {
    ui.error = null;
    renderErrorBanner();
  };
}

// Messages from the generated app (it runs on another origin, so this is the only channel).
window.addEventListener("message", (event) => {
  const frame = $("#preview");
  if (!frame || event.source !== frame.contentWindow || !event.data || event.data.source !== "naturalsketch") return;
  if (event.data.type === "page" && event.data.page !== ui.page) {
    ui.page = event.data.page;
    ui.loadedSrc = previewSrc();
    ui.error = null;
    renderToolbar();
    renderErrorBanner();
  }
  if (event.data.type === "error" && !busy()) {
    ui.error = { page: event.data.page, message: event.data.message };
    renderErrorBanner();
    autoFix(ui.error);
  }
});

// React projects: when the preview reports an error, ask the AI to fix it straight away (at most
// twice per version, and only on the current version; the server enforces the same limit).
function autoFix(error) {
  const p = ui.project;
  if (!isReact() || !p || ui.viewVersion !== p.current || busy()) return;
  ui.autoFixes = ui.autoFixes || {};
  const key = `${p.id}:${p.current}`;
  if ((ui.autoFixes[key] || 0) >= 2) return;
  ui.autoFixes[key] = (ui.autoFixes[key] || 0) + 1;
  ui.error = null;
  renderErrorBanner();
  flash("The preview hit an error. Fixing it automatically…", "ok");
  sendMessage(p.id, `Fix this error in the preview (${error.page} page): ${error.message}`, { auto: true });
}

async function sendMessage(pid, text, options = {}) {
  text = text.trim();
  if (!text || busy()) return;
  if (!options.auto) $("#message").value = "";
  try {
    await api(`/api/projects/${pid}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text, auto: Boolean(options.auto) }) });
    if (!options.auto) refreshUsage();
  } catch (e) {
    flash(e.message);
    if (!options.auto && $("#message") && !$("#message").value) $("#message").value = text; // don't lose what they typed
  }
  refresh(pid);
}

async function rebuild(pid) {
  try {
    await api(`/api/projects/${pid}/rebuild`, { method: "POST" });
  } catch (e) {
    flash(e.message);
  }
  refresh(pid);
}

async function restore(pid, version) {
  try {
    await api(`/api/projects/${pid}/restore`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version }) });
  } catch (e) {
    flash(e.message);
  }
  ui.viewVersion = null;
  refresh(pid);
}

// What an email link or Google brought back in the address, read before supabase-js tidies it.
// Confirmation links often open in another browser (e.g. a mail app's), where the sign-in can't
// be finished automatically even though the email is now confirmed; then we say so.
function readAuthReturn() {
  const query = new URLSearchParams(location.search);
  const hash = new URLSearchParams(location.hash.startsWith("#") && !location.hash.startsWith("#/") ? location.hash.slice(1) : "");
  const get = (k) => query.get(k) || hash.get(k);
  return { code: Boolean(get("code")), error: get("error_description") || get("error"), type: get("type"), from: query.get("from") };
}

function tidyAuthAddress() {
  const hash = location.hash.startsWith("#/") ? location.hash : "";
  if (location.search || location.hash !== hash) history.replaceState(null, "", location.pathname + hash);
}

function explainAuthReturn(back) {
  if (!authState.enabled) return;
  if (back.error) {
    const expired = /expired|invalid/i.test(back.error);
    openAuth("signin", expired
      ? "That link has expired or was already used. Sign in below, or use “Forgot password?” to get a new link."
      : `Sign-in didn't finish: ${back.error.replace(/\+/g, " ")}`);
  } else if (back.code && !signedIn()) {
    if (back.from === "reset") {
      openAuth("forgot", "Open the reset link in the same browser where you asked for it. Or send a new link from here.");
    } else if (back.from === "confirm") {
      openAuth("signin", "Your email is confirmed. Sign in with your email and password to continue.");
    } else {
      openAuth("signin", "Please sign in to continue.");
    }
  }
}

// Startup: draw the page straight away, then fetch server settings and the signed-in user.
(async () => {
  const authReturn = readAuthReturn();
  // Coming back from Google sign-in: return to where the person was.
  try {
    const back = sessionStorage.getItem("ns:return");
    if (back != null) {
      sessionStorage.removeItem("ns:return");
      if (back && back !== location.hash) history.replaceState(null, "", location.pathname + location.search + back);
    }
  } catch (e) {
    /* fine without */
  }
  route();

  // A sleeping free-plan server can take up to a minute to wake, and the Vercel proxy gives up
  // sooner, so early requests may fail. Keep trying for about 90 s before calling it down.
  let status = null;
  // Retry every 3 s for about 90 s, so the page continues as soon as the server is awake.
  const giveUpAt = Date.now() + 90000;
  while (!status && Date.now() < giveUpAt) {
    try {
      status = await api("/api/status");
    } catch (e) {
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
  if (status) {
    ui.status = status;
    if (status.preview_origin) PREVIEW = status.preview_origin;
    try {
      await initAuth(status.auth); // auth: null only when the server really has accounts off
    } catch (e) {
      flash(e.message);
    }
  } else {
    ui.serverDown = true;
  }
  ui.booting = false;
  markReady();
  renderAccount();
  if (authReturn.code || authReturn.error) {
    tidyAuthAddress();
    if (authReturn.type !== "recovery") explainAuthReturn(authReturn);
  }
})();
