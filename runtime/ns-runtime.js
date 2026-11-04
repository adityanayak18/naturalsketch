/*
 * NaturalSketch runtime: the shared layer every generated prototype runs on.
 * Provides the navigation shell, a persistent mock database, app state,
 * mock auth, toasts and dialogs, so generated pages only contain page content.
 */
(function () {
  "use strict";

  const CFG = window.NS_CONFIG || {};
  const SEED = window.NS_DATA || {};
  const KEY = "ns:" + (CFG.id || "app");
  const PAGES = CFG.pages || [];
  const page = document.documentElement.getAttribute("data-page") || "index";
  const pageCfg = PAGES.find((p) => p.slug === page) || {};
  const listeners = {};

  // ---------------------------------------------------------------- persistence
  // localStorage when available; where it is blocked (sandboxed frames, strict privacy
  // settings) state rides along in window.name, which survives page navigations.
  const backend = (() => {
    try {
      localStorage.setItem("__ns", "1");
      localStorage.removeItem("__ns");
      return "local";
    } catch (e) {
      return "name";
    }
  })();

  function readAll() {
    try {
      if (backend === "local") return JSON.parse(localStorage.getItem(KEY) || "{}");
      const all = JSON.parse(window.name || "{}");
      return (all && all[KEY]) || {};
    } catch (e) {
      return {};
    }
  }

  let state = readAll();

  function persist() {
    try {
      if (backend === "local") {
        localStorage.setItem(KEY, JSON.stringify(state));
        return;
      }
      let all = {};
      try {
        all = JSON.parse(window.name || "{}") || {};
      } catch (e) {}
      all[KEY] = state;
      window.name = JSON.stringify(all);
    } catch (e) {}
  }

  const clone = (value) => (value === undefined ? undefined : JSON.parse(JSON.stringify(value)));

  function emit(key) {
    const value = key.startsWith("db:") ? db(key.slice(3)).all() : clone(state[key]);
    (listeners[key] || []).forEach((fn) => {
      try {
        fn(value);
      } catch (e) {
        console.error(e);
      }
    });
    updateBadges();
  }

  function on(key, fn) {
    (listeners[key] = listeners[key] || []).push(fn);
    return () => (listeners[key] = listeners[key].filter((f) => f !== fn));
  }

  // ---------------------------------------------------------------- state + db
  const store = {
    get: (key, fallback) => (key in state ? clone(state[key]) : clone(fallback)),
    set(key, value) {
      state[key] = clone(value);
      persist();
      emit(key);
      return value;
    },
    update: (key, fn, fallback) => store.set(key, fn(store.get(key, fallback))),
    remove(key) {
      delete state[key];
      persist();
      emit(key);
    },
  };

  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const same = (a, b) => String(a) === String(b);

  function db(name) {
    const key = "db:" + name;
    const rows = () => {
      if (!(key in state)) state[key] = clone(SEED[name] || []);
      return state[key];
    };
    const save = () => {
      persist();
      emit(key);
    };
    return {
      all: () => clone(rows()),
      get: (id) => clone(rows().find((r) => same(r.id, id)) || null),
      find: (fn) => clone(rows().find(fn) || null),
      filter: (fn) => clone(rows().filter(fn)),
      count: (fn) => (fn ? rows().filter(fn).length : rows().length),
      add(item) {
        const record = Object.assign({ id: uid(), createdAt: new Date().toISOString() }, item);
        rows().unshift(record);
        save();
        return clone(record);
      },
      update(id, patch) {
        const record = rows().find((r) => same(r.id, id));
        if (!record) return null;
        Object.assign(record, typeof patch === "function" ? patch(clone(record)) : patch);
        save();
        return clone(record);
      },
      remove(id) {
        const list = rows();
        const index = list.findIndex((r) => same(r.id, id));
        if (index < 0) return false;
        list.splice(index, 1);
        save();
        return true;
      },
    };
  }

  // ---------------------------------------------------------------- navigation
  const slugOf = (slug) => String(slug || "index").replace(/\.html$/, "");

  function href(slug, params) {
    const query = params && Object.keys(params).length ? "?" + new URLSearchParams(params).toString() : "";
    return slugOf(slug) + ".html" + query;
  }

  function go(slug, params) {
    location.href = href(slug, params);
  }

  const params = () => Object.fromEntries(new URLSearchParams(location.search));

  // ---------------------------------------------------------------- auth
  const loginSlug = (CFG.auth && CFG.auth.login_page) || "login";

  const auth = {
    user: () => store.get("auth:user", null),
    login(user) {
      user = Object.assign({}, user || {});
      if (!user.name) user.name = user.email ? String(user.email).split("@")[0] : "Guest";
      store.set("auth:user", user);
      return user;
    },
    logout() {
      store.remove("auth:user");
      go("index");
    },
    require() {
      if (auth.user()) return true;
      go(loginSlug, { next: page });
      return false;
    },
  };

  if (CFG.auth && CFG.auth.enabled && pageCfg.auth_required && !auth.user()) {
    location.replace(href(loginSlug, { next: page }));
  }

  // ---------------------------------------------------------------- helpers
  const esc = (text) =>
    String(text == null ? "" : text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function money(amount) {
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: CFG.currency || "USD" }).format(Number(amount) || 0);
    } catch (e) {
      return (Number(amount) || 0).toFixed(2);
    }
  }

  function date(value) {
    const d = new Date(value);
    return isNaN(d) ? String(value || "") : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function icons() {
    if (window.lucide) window.lucide.createIcons();
  }

  function toast(message, type) {
    let host = document.getElementById("ns-toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "ns-toasts";
      host.className = "fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none";
      document.body.appendChild(host);
    }
    const colors = { success: "bg-emerald-600", error: "bg-red-600", info: "bg-slate-800" };
    const icon = { success: "check-circle", error: "alert-circle", info: "info" }[type || "success"] || "info";
    const el = document.createElement("div");
    el.className = `pointer-events-auto flex items-center gap-2 text-white text-sm px-4 py-3 rounded-xl shadow-lg transition-all duration-300 ${colors[type || "success"] || colors.info}`;
    el.innerHTML = `<i data-lucide="${icon}" class="w-4 h-4"></i><span>${esc(message)}</span>`;
    host.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      el.style.transform = "translateY(8px)";
      setTimeout(() => el.remove(), 300);
    }, 2800);
  }

  function modal(options) {
    const o = Object.assign({ title: "", html: "", confirmText: "OK", cancelText: "Cancel" }, options || {});
    const overlay = document.createElement("div");
    overlay.className = "fixed inset-0 z-[90] bg-black/50 backdrop-blur-sm flex items-center justify-center p-4";
    overlay.innerHTML = `
      <div class="bg-surface text-foreground w-full max-w-md rounded-2xl shadow-2xl border border-border">
        ${o.title ? `<div class="px-6 pt-5 text-lg font-semibold font-heading">${esc(o.title)}</div>` : ""}
        <div class="px-6 py-4 text-sm" data-ns-modal-body>${o.html}</div>
        <div class="px-6 pb-5 flex justify-end gap-2">
          ${o.cancelText ? `<button data-ns-cancel class="px-4 py-2 rounded-lg text-sm border border-border hover:bg-foreground/5">${esc(o.cancelText)}</button>` : ""}
          <button data-ns-confirm class="px-4 py-2 rounded-lg text-sm bg-primary text-primary-foreground hover:opacity-90">${esc(o.confirmText)}</button>
        </div>
      </div>`;
    const close = () => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => e.key === "Escape" && close();
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.closest("[data-ns-cancel]")) close();
      if (e.target.closest("[data-ns-confirm]")) {
        const body = overlay.querySelector("[data-ns-modal-body]");
        if (!o.onConfirm || o.onConfirm(body) !== false) close();
      }
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    return close;
  }

  // ---------------------------------------------------------------- shell
  function badgeCount(key) {
    if (key.startsWith("db:")) return db(key.slice(3)).count();
    const value = store.get(key, null);
    if (Array.isArray(value)) return value.reduce((sum, item) => sum + (Number(item && item.qty) || 1), 0);
    return Number(value) || 0;
  }

  function updateBadges() {
    document.querySelectorAll("[data-ns-badge]").forEach((el) => {
      const count = badgeCount(el.getAttribute("data-ns-badge"));
      el.textContent = count;
      el.classList.toggle("hidden", !count);
    });
  }

  function navLinks(vertical) {
    return PAGES.filter((p) => p.nav)
      .map((p) => {
        const active = p.slug === page;
        const tone = active ? "bg-primary/10 text-primary" : "text-muted hover:text-foreground hover:bg-foreground/5";
        const badge = p.badge
          ? `<span data-ns-badge="${esc(p.badge)}" class="hidden ${vertical ? "ml-auto" : "ml-1"} min-w-[1.25rem] h-5 px-1.5 rounded-full bg-primary text-primary-foreground text-[11px] font-semibold grid place-items-center"></span>`
          : "";
        return `<a href="${p.slug}.html" class="flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${tone}">
          <i data-lucide="${esc(p.icon || "circle")}" class="w-4 h-4"></i><span>${esc(p.title)}</span>${badge}</a>`;
      })
      .join("");
  }

  function userArea(vertical) {
    if (!CFG.auth || !CFG.auth.enabled) return "";
    const user = auth.user();
    if (!user) {
      return `<a href="${loginSlug}.html" class="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:opacity-90">Sign in</a>`;
    }
    const initial = esc(String(user.name || "?").charAt(0).toUpperCase());
    return `<div class="flex items-center gap-2 ${vertical ? "w-full" : ""}">
      <span class="w-8 h-8 rounded-full bg-primary/15 text-primary grid place-items-center text-sm font-semibold">${initial}</span>
      <span class="text-sm font-medium ${vertical ? "flex-1 truncate" : "hidden lg:inline"}">${esc(user.name)}</span>
      <button data-ns-logout title="Sign out" class="p-2 rounded-lg text-muted hover:text-foreground hover:bg-foreground/5"><i data-lucide="log-out" class="w-4 h-4"></i></button>
    </div>`;
  }

  function logo() {
    return `<a href="index.html" class="flex items-center gap-2 font-heading font-bold text-lg text-foreground shrink-0">
      <span class="w-8 h-8 rounded-lg bg-primary text-primary-foreground grid place-items-center"><i data-lucide="${esc(CFG.logo_icon || "sparkles")}" class="w-4 h-4"></i></span>
      <span>${esc(CFG.app_name || "App")}</span></a>`;
  }

  function topLayout() {
    return `
      <header class="sticky top-0 z-40 bg-surface/85 backdrop-blur border-b border-border">
        <div class="max-w-7xl mx-auto px-4 h-16 flex items-center gap-4">
          ${logo()}
          <nav class="hidden md:flex items-center gap-1 ml-4">${navLinks(false)}</nav>
          <div class="ml-auto flex items-center gap-2">${userArea(false)}
            <button data-ns-menu class="md:hidden p-2 rounded-lg hover:bg-foreground/5"><i data-lucide="menu" class="w-5 h-5"></i></button>
          </div>
        </div>
        <nav data-ns-mobile class="hidden md:hidden border-t border-border px-4 py-2 flex-col gap-1">${navLinks(true)}</nav>
      </header>
      <main data-ns-slot></main>
      <footer class="border-t border-border mt-16">
        <div class="max-w-7xl mx-auto px-4 py-8 text-sm text-muted flex flex-col sm:flex-row gap-2 justify-between">
          <span>© ${new Date().getFullYear()} ${esc(CFG.app_name || "")}</span><span>${esc(CFG.tagline || "")}</span>
        </div>
      </footer>`;
  }

  function sideLayout() {
    return `
      <aside data-ns-sidebar class="fixed inset-y-0 left-0 z-40 w-64 bg-surface border-r border-border flex flex-col -translate-x-full md:translate-x-0 transition-transform">
        <div class="h-16 px-5 flex items-center">${logo()}</div>
        <nav class="flex-1 px-3 py-2 flex flex-col gap-1 overflow-y-auto">${navLinks(true)}</nav>
        <div class="p-3 border-t border-border empty:hidden">${userArea(true)}</div>
      </aside>
      <div class="md:ml-64 min-h-screen flex flex-col min-w-0">
        <header class="md:hidden sticky top-0 z-30 h-14 bg-surface border-b border-border flex items-center gap-3 px-4">
          <button data-ns-menu class="p-2 -ml-2 rounded-lg hover:bg-foreground/5"><i data-lucide="menu" class="w-5 h-5"></i></button>
          <span class="font-heading font-bold">${esc(CFG.app_name || "")}</span>
        </header>
        <main data-ns-slot class="flex-1"></main>
      </div>`;
  }

  function mountShell() {
    const content = document.getElementById("ns-page");
    if (!content || pageCfg.shell === false) return;
    const shell = document.createElement("div");
    shell.id = "ns-shell";
    shell.innerHTML = CFG.layout === "sidebar" ? sideLayout() : topLayout();
    const slot = shell.querySelector("[data-ns-slot]");
    content.replaceWith(shell);
    slot.appendChild(content);
  }

  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-ns-logout]")) auth.logout();
    if (e.target.closest("[data-ns-menu]")) {
      const sidebar = document.querySelector("[data-ns-sidebar]");
      if (sidebar) sidebar.classList.toggle("-translate-x-full");
      const mobile = document.querySelector("[data-ns-mobile]");
      if (mobile) {
        mobile.classList.toggle("hidden");
        mobile.classList.toggle("flex");
      }
    }
  });

  // ---------------------------------------------------------------- builder bridge
  const tell = (message) => {
    try {
      if (window.parent !== window) window.parent.postMessage(Object.assign({ source: "naturalsketch" }, message), "*");
    } catch (e) {}
  };

  window.addEventListener("error", (e) => tell({ type: "error", page, message: String(e.message || e.error || "Script error") }));
  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "ns:reset") {
      state = {};
      persist();
      location.reload();
    }
  });

  // Broken images (stock photo hiccups, bad URLs) become a neutral placeholder instead of a gap.
  const PLACEHOLDER =
    "data:image/svg+xml," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e2e8f0"/><stop offset="1" stop-color="#cbd5e1"/></linearGradient></defs><rect width="600" height="400" fill="url(#g)"/><path d="M250 250l40-50 30 35 20-25 60 70H230z" fill="#94a3b8"/><circle cx="270" cy="170" r="16" fill="#94a3b8"/></svg>',
    );
  document.addEventListener(
    "error",
    (e) => {
      const img = e.target;
      if (img && img.tagName === "IMG" && img.src !== PLACEHOLDER) img.src = PLACEHOLDER;
    },
    true,
  );

  // Turn <i data-lucide> placeholders into icons whenever new markup appears.
  let iconTimer = null;
  new MutationObserver(() => {
    clearTimeout(iconTimer);
    iconTimer = setTimeout(() => document.querySelector("i[data-lucide]") && icons(), 30);
  }).observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener("DOMContentLoaded", () => {
    mountShell();
    updateBadges();
    icons();
    tell({ type: "page", page, title: document.title });
  });

  window.NS = { config: CFG, data: SEED, db, store, on, auth, go, href, params, toast, modal, money, date, esc, icons };
})();
