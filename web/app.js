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
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error("Sign in to continue");
  }
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

// Shown when the server has a password set (NS_ACCESS_PASSWORD) and this browser isn't signed in.
function showLogin() {
  if (ui.needLogin) return;
  ui.needLogin = true;
  clearTimeout(ui.poll);
  teardownHome();
  document.body.className = "mode-ws";
  document.title = "Sign in · NaturalSketch";
  app.innerHTML = `
    <div class="login">
      <form id="login" class="login-card">
        <span class="brand-mark"><i data-lucide="pen-tool"></i></span>
        <h1>Natural<em>Sketch</em></h1>
        <p>This studio is private. Enter the password to continue.</p>
        <input id="password" type="password" autocomplete="current-password" placeholder="Password" required>
        <button class="btn btn-accent" type="submit"><i data-lucide="log-in"></i>Sign in</button>
      </form>
    </div>`;
  icons();
  $("#password").focus();
  $("#login").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: $("#password").value }) });
      location.reload();
    } catch (err) {
      flash(err.message);
      $("#password").select();
    }
  });
}

function timeAgo(seconds) {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

function flash(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "alert");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.classList.add("out"), 3600);
  setTimeout(() => el.remove(), 4000);
}

const TEMPLATES = [
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

const STEPS = [
  { title: "Analyse", tag: "vision model · opencv", text: "A vision model reads your images: layout, components, text and style. Paper sketches are cleaned up with OpenCV first, lifting shadows and sharpening the lines, so nothing you drew is missed." },
  { title: "Plan", tag: "pages · navigation · theme · data", text: "The app gets a name, pages, navigation, a theme and realistic sample data. Every feature you listed is mapped to the page that will carry it." },
  { title: "Build", tag: "parallel pages · shared runtime", text: "Pages are written in parallel on a shared runtime: navigation shell, a persistent mock database, cart and wishlist state, mock login, toasts and dialogs. Buttons and forms actually work." },
  { title: "Iterate", tag: "chat · versions · restore", text: "Ask for changes in plain words. Pages are added, edited or removed; theme and name changes apply instantly. Every change becomes a version you can open and restore." },
  { title: "Export", tag: "zip · plain html", text: "Download a ZIP of plain HTML that runs anywhere: Netlify Drop, GitHub Pages, Vercel or any static host. Data and logins live in the browser, so no backend is needed." },
];

const CHAIN_LABELS = { VISION: "Image analysis", PLAN: "Planning", CODE: "Page code", CHAT: "Chat edits" };

const ui = { files: [], status: null, project: null, page: "index", viewVersion: null, device: "desktop", showCode: false, poll: null, error: null, loadedSrc: null };

// Shared with scene3d.js; GSAP tweens these numbers as you scroll.
const sceneState = { x: 2.55, y: 0, scale: 1, rotX: 0.18, rotY: -0.42, spread: 0, fan: 0, active: 0, morph: 0, pencil: 0, fade: 1 };
const SCENE_START = { ...sceneState };

// ------------------------------------------------------------------ routing

function route() {
  if (ui.needLogin) return;
  clearTimeout(ui.poll);
  teardownHome();
  const match = location.hash.match(/^#\/p\/([a-f0-9]+)/);
  if (match) openProject(match[1]);
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
          <div id="providers" class="providers"></div>
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
            </div>
            <div class="composer-foot">
              <p class="fine">Powered by Gemini, Groq and OpenRouter free tiers: if one is busy, the next takes over.</p>
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
  try {
    ui.status = await api("/api/status");
  } catch (e) {
    return;
  }
  const host = $("#providers");
  if (!host) return;
  host.innerHTML = Object.entries(ui.status.providers)
    .map(([name, ok]) => `<span class="provider ${ok ? "on" : ""}" title="${ok ? "API key configured" : "No API key"}"><i></i>${esc(name)}</span>`)
    .join("");
  const chains = $("#chains");
  if (chains && ui.status.chains) {
    const cooling = ui.status.cooling_down || {};
    chains.innerHTML = Object.entries(ui.status.chains)
      .map(([task, models]) => `<dt>${esc(CHAIN_LABELS[task.toUpperCase()] || task)}</dt><dd>${models.map((m) => `<span class="${cooling[m] ? "cooling" : ""}" title="${cooling[m] ? `Cooling down, ${cooling[m]}s` : ""}">${esc(m)}</span>`).join("<b>→</b>")}</dd>`)
      .join("");
  }
}

async function loadProjects() {
  const host = $("#project-list");
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
      const thumb = p.current
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
          <button type="button" data-delete="${p.id}" title="Delete" aria-label="Delete ${esc(p.name)}" class="card-del"><i data-lucide="trash-2"></i></button>
        </a>`;
    })
    .join("");
  icons();
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

  $$("[data-template]").forEach((button) =>
    button.addEventListener("click", () => {
      const t = TEMPLATES[button.dataset.template];
      $("#prompt").value = t.prompt;
      $("#features").value = t.features;
      $$("[data-template]").forEach((b) => b.classList.toggle("on", b === button));
    }),
  );

  $("#composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const prompt = $("#prompt").value.trim();
    if (!prompt && !ui.files.length) {
      flash("Add a sketch or describe your app first.");
      return;
    }
    const button = $("#build");
    button.disabled = true;
    button.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Starting…</span>`;
    icons();
    const form = new FormData();
    form.append("prompt", prompt);
    form.append("features", $("#features").value);
    ui.files.forEach(({ file }) => form.append("images", file));
    try {
      const { id } = await api("/api/projects", { method: "POST", body: form });
      ui.files = [];
      location.hash = `#/p/${id}`;
    } catch (err) {
      flash(err.message);
      button.disabled = false;
      button.innerHTML = `<i data-lucide="sparkles"></i><span>Build app</span>`;
      icons();
    }
  });
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
          <a id="download" class="btn btn-accent sm"><i data-lucide="download"></i>Export</a>
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
    if (ui.needLogin) return;
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
  $("#open-tab").href = ready ? `${PREVIEW}/preview/${p.id}/${ui.viewVersion}/${ui.page}.html` : "#";
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
    ? SUGGESTIONS.map((s) => `<button type="button" class="chip">${esc(s)}</button>`).join("")
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
      ui.loadedSrc = null;
      renderToolbar();
      renderStage();
      renderHeader();
    }),
  );
  $$("[data-device]").forEach((b) => b.classList.toggle("on", b.dataset.device === ui.device && !ui.showCode));
  $("#code").classList.toggle("on", ui.showCode);
  if (p) $("#open-tab").href = ui.viewVersion ? `${PREVIEW}/preview/${p.id}/${ui.viewVersion}/${ui.page}.html` : "#";
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

  const src = `${PREVIEW}/preview/${p.id}/${ui.viewVersion}/${ui.page}.html`;
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
  if (existing && ui.loadedSrc === src) {
    const frame = existing.closest(".device-frame");
    frame.style.width = width;
    frame.dataset.device = ui.device;
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
    ui.loadedSrc = `${PREVIEW}/preview/${ui.project.id}/${ui.viewVersion}/${ui.page}.html`;
    ui.error = null;
    renderToolbar();
    renderErrorBanner();
  }
  if (event.data.type === "error" && !busy()) {
    ui.error = { page: event.data.page, message: event.data.message };
    renderErrorBanner();
  }
});

async function sendMessage(pid, text) {
  text = text.trim();
  if (!text || busy()) return;
  $("#message").value = "";
  try {
    await api(`/api/projects/${pid}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
  } catch (e) {
    flash(e.message);
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

api("/api/status")
  .then((status) => {
    ui.status = status;
    if (status.preview_origin) PREVIEW = status.preview_origin;
  })
  .catch(() => {})
  .finally(route);
