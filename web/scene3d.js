/* NaturalSketch home scene: a stack of drawing sheets and a pencil, driven by page scroll.
   Each sheet starts as a pencil wireframe and is "built" into a finished screen as you scroll.
   app.js owns the `state` object and tweens it with GSAP ScrollTrigger; this file only renders it. */
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js";

const N = 5;
const W = 512;
const H = 682;
const ACCENTS = ["#b4441c", "#2f4a3a", "#24426b", "#c38a22", "#7a2e3a"];

let current = null;

// ------------------------------------------------------------------ sheet artwork

function seeded(seed) {
  let s = seed * 9973 + 17;
  return () => ((s = (s * 16807) % 2147483647) / 2147483647);
}

// Each layout is a list of blocks in a 0..1 coordinate space.
const LAYOUTS = [
  // landing page
  [
    ["nav", 0.06, 0.05, 0.88, 0.06], ["img", 0.06, 0.15, 0.88, 0.3], ["title", 0.06, 0.49, 0.6, 0.04],
    ["lines", 0.06, 0.56, 0.75, 0.08], ["btn", 0.06, 0.68, 0.28, 0.05],
    ["card", 0.06, 0.78, 0.27, 0.16], ["card", 0.365, 0.78, 0.27, 0.16], ["card", 0.67, 0.78, 0.27, 0.16],
  ],
  // product grid
  [
    ["nav", 0.06, 0.05, 0.88, 0.06], ["field", 0.06, 0.15, 0.88, 0.05],
    ["card", 0.06, 0.24, 0.42, 0.22], ["card", 0.52, 0.24, 0.42, 0.22],
    ["card", 0.06, 0.5, 0.42, 0.22], ["card", 0.52, 0.5, 0.42, 0.22],
    ["card", 0.06, 0.76, 0.42, 0.19], ["card", 0.52, 0.76, 0.42, 0.19],
  ],
  // dashboard
  [
    ["side", 0.06, 0.05, 0.2, 0.9], ["kpi", 0.31, 0.05, 0.19, 0.1], ["kpi", 0.53, 0.05, 0.19, 0.1], ["kpi", 0.75, 0.05, 0.19, 0.1],
    ["chart", 0.31, 0.19, 0.63, 0.32], ["rows", 0.31, 0.56, 0.63, 0.39],
  ],
  // detail page
  [
    ["nav", 0.06, 0.05, 0.88, 0.06], ["img", 0.06, 0.15, 0.45, 0.42], ["title", 0.56, 0.16, 0.38, 0.04],
    ["lines", 0.56, 0.23, 0.38, 0.16], ["btn", 0.56, 0.44, 0.3, 0.05], ["title", 0.06, 0.63, 0.4, 0.035],
    ["lines", 0.06, 0.69, 0.88, 0.1], ["lines", 0.06, 0.83, 0.88, 0.1],
  ],
  // form / checkout
  [
    ["nav", 0.06, 0.05, 0.88, 0.06], ["title", 0.06, 0.16, 0.5, 0.04], ["field", 0.06, 0.24, 0.55, 0.05],
    ["field", 0.06, 0.32, 0.55, 0.05], ["field", 0.06, 0.4, 0.55, 0.05], ["field", 0.06, 0.48, 0.55, 0.05],
    ["btn", 0.06, 0.57, 0.3, 0.05], ["card", 0.66, 0.24, 0.28, 0.38], ["rows", 0.06, 0.68, 0.88, 0.27],
  ],
];

function sketchLine(ctx, rnd, x1, y1, x2, y2) {
  for (let pass = 0; pass < 2; pass++) {
    const j = () => (rnd() - 0.5) * 2.4;
    ctx.beginPath();
    ctx.moveTo(x1 + j(), y1 + j());
    const mx = (x1 + x2) / 2 + j() * 1.5;
    const my = (y1 + y2) / 2 + j() * 1.5;
    ctx.quadraticCurveTo(mx, my, x2 + j(), y2 + j());
    ctx.globalAlpha = pass ? 0.35 : 0.85;
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function sketchRect(ctx, rnd, x, y, w, h) {
  sketchLine(ctx, rnd, x, y, x + w, y);
  sketchLine(ctx, rnd, x + w, y, x + w, y + h);
  sketchLine(ctx, rnd, x + w, y + h, x, y + h);
  sketchLine(ctx, rnd, x, y + h, x, y);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

function drawSheet(variant, built) {
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  const rnd = seeded(variant + 1);
  const accent = ACCENTS[variant % ACCENTS.length];

  // paper
  ctx.fillStyle = built ? "#fbf8f1" : "#f5efe1";
  ctx.fillRect(0, 0, W, H);
  if (!built) {
    ctx.strokeStyle = "rgba(58, 96, 150, 0.12)";
    ctx.lineWidth = 1;
    for (let x = 16; x < W; x += 22) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke(); }
    for (let y = 16; y < H; y += 22) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  }

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  const P = (v, axis) => v * (axis ? H : W);

  for (const [type, bx, by, bw, bh] of LAYOUTS[variant % LAYOUTS.length]) {
    const x = P(bx, 0), y = P(by, 1), w = P(bw, 0), h = P(bh, 1);
    if (!built) {
      ctx.strokeStyle = "#2a2722";
      ctx.lineWidth = 2.2;
      if (type === "img" || type === "card" || type === "chart") {
        sketchRect(ctx, rnd, x, y, w, h);
        if (type === "chart") {
          const pts = 7;
          for (let i = 0; i < pts - 1; i++) {
            const a = 0.3 + 0.5 * Math.sin(i * 1.3 + variant), b = 0.3 + 0.5 * Math.sin((i + 1) * 1.3 + variant);
            sketchLine(ctx, rnd, x + (w * i) / (pts - 1), y + h * (1 - a * 0.8), x + (w * (i + 1)) / (pts - 1), y + h * (1 - b * 0.8));
          }
        } else {
          const ih = type === "card" ? h * 0.55 : h;
          sketchLine(ctx, rnd, x, y, x + w, y + ih);
          sketchLine(ctx, rnd, x + w, y, x, y + ih);
          if (type === "card") {
            sketchLine(ctx, rnd, x, y + ih, x + w, y + ih);
            sketchLine(ctx, rnd, x + 8, y + ih + 14, x + w * 0.7, y + ih + 14);
            sketchLine(ctx, rnd, x + 8, y + ih + 26, x + w * 0.45, y + ih + 26);
          }
        }
      } else if (type === "nav") {
        sketchRect(ctx, rnd, x, y, w, h);
        ctx.beginPath(); ctx.arc(x + 18, y + h / 2, 7, 0, Math.PI * 2); ctx.stroke();
        for (let i = 0; i < 3; i++) sketchLine(ctx, rnd, x + w - 40 - i * 52, y + h / 2, x + w - 10 - i * 52 - 12, y + h / 2);
      } else if (type === "title") {
        ctx.lineWidth = 4;
        sketchLine(ctx, rnd, x, y + h / 2, x + w, y + h / 2);
      } else if (type === "lines") {
        const n = Math.max(2, Math.round(h / 14));
        for (let i = 0; i < n; i++) sketchLine(ctx, rnd, x, y + i * 14 + 4, x + w * (i === n - 1 ? 0.6 : 0.96), y + i * 14 + 4);
      } else if (type === "btn" || type === "field") {
        sketchRect(ctx, rnd, x, y, w, h);
        if (type === "btn") sketchLine(ctx, rnd, x + w * 0.25, y + h / 2, x + w * 0.75, y + h / 2);
      } else if (type === "side") {
        sketchRect(ctx, rnd, x, y, w, h);
        for (let i = 0; i < 6; i++) sketchLine(ctx, rnd, x + 10, y + 30 + i * 30, x + w - 14, y + 30 + i * 30);
      } else if (type === "kpi") {
        sketchRect(ctx, rnd, x, y, w, h);
        ctx.lineWidth = 3.5;
        sketchLine(ctx, rnd, x + 10, y + h * 0.62, x + w * 0.6, y + h * 0.62);
      } else if (type === "rows") {
        for (let i = 0; i < 6; i++) sketchLine(ctx, rnd, x, y + i * (h / 6) + 8, x + w, y + i * (h / 6) + 8);
      }
    } else {
      // the finished screen
      if (type === "nav") {
        ctx.fillStyle = "#1c1a16"; roundRect(ctx, x, y, w, h, 8); ctx.fill();
        ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(x + 20, y + h / 2, 8, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,.55)";
        for (let i = 0; i < 3; i++) { roundRect(ctx, x + w - 52 - i * 52, y + h / 2 - 3, 34, 6, 3); ctx.fill(); }
      } else if (type === "img" || type === "chart") {
        const g = ctx.createLinearGradient(x, y, x + w, y + h);
        g.addColorStop(0, accent); g.addColorStop(1, "#e9d9b8");
        ctx.fillStyle = type === "chart" ? "#ffffff" : g; roundRect(ctx, x, y, w, h, 10); ctx.fill();
        if (type === "chart") {
          ctx.strokeStyle = "#e6dccb"; ctx.lineWidth = 1; ctx.stroke();
          for (let i = 0; i < 9; i++) {
            const v = 0.25 + 0.6 * Math.abs(Math.sin(i * 1.1 + variant));
            ctx.fillStyle = i % 3 === 2 ? accent : "#d9cdb5";
            roundRect(ctx, x + 14 + i * ((w - 28) / 9), y + h - 12 - (h - 30) * v, (w - 28) / 9 - 8, (h - 30) * v, 3); ctx.fill();
          }
        } else {
          ctx.fillStyle = "rgba(255,255,255,.35)"; ctx.beginPath(); ctx.arc(x + w * 0.72, y + h * 0.35, h * 0.16, 0, Math.PI * 2); ctx.fill();
        }
      } else if (type === "card") {
        ctx.fillStyle = "#ffffff"; roundRect(ctx, x, y, w, h, 10); ctx.fill();
        ctx.strokeStyle = "#e6dccb"; ctx.lineWidth = 1.5; ctx.stroke();
        const g = ctx.createLinearGradient(x, y, x + w, y + h * 0.55);
        g.addColorStop(0, "#e9d9b8"); g.addColorStop(1, accent);
        ctx.fillStyle = g; roundRect(ctx, x + 6, y + 6, w - 12, h * 0.55 - 6, 7); ctx.fill();
        ctx.fillStyle = "#3d3830"; roundRect(ctx, x + 10, y + h * 0.55 + 10, w * 0.6, 7, 3); ctx.fill();
        ctx.fillStyle = "#cfc5b2"; roundRect(ctx, x + 10, y + h * 0.55 + 24, w * 0.4, 6, 3); ctx.fill();
      } else if (type === "title") {
        ctx.fillStyle = "#1c1a16"; roundRect(ctx, x, y, w, h, 4); ctx.fill();
      } else if (type === "lines") {
        const n = Math.max(2, Math.round(h / 14));
        ctx.fillStyle = "#cfc5b2";
        for (let i = 0; i < n; i++) { roundRect(ctx, x, y + i * 14, w * (i === n - 1 ? 0.6 : 0.96), 6, 3); ctx.fill(); }
      } else if (type === "btn") {
        ctx.fillStyle = accent; roundRect(ctx, x, y, w, h, h / 2); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,.8)"; roundRect(ctx, x + w * 0.28, y + h / 2 - 3, w * 0.44, 6, 3); ctx.fill();
      } else if (type === "field") {
        ctx.fillStyle = "#ffffff"; roundRect(ctx, x, y, w, h, 7); ctx.fill();
        ctx.strokeStyle = "#d6ccb8"; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.fillStyle = "#d9cfbd"; roundRect(ctx, x + 10, y + h / 2 - 3, w * 0.35, 6, 3); ctx.fill();
      } else if (type === "side") {
        ctx.fillStyle = "#1c1a16"; roundRect(ctx, x, y, w, h, 10); ctx.fill();
        for (let i = 0; i < 6; i++) {
          ctx.fillStyle = i === 1 ? accent : "rgba(255,255,255,.35)";
          roundRect(ctx, x + 12, y + 26 + i * 30, w - 24, 8, 4); ctx.fill();
        }
      } else if (type === "kpi") {
        ctx.fillStyle = "#ffffff"; roundRect(ctx, x, y, w, h, 9); ctx.fill();
        ctx.strokeStyle = "#e6dccb"; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.fillStyle = "#cfc5b2"; roundRect(ctx, x + 10, y + 12, w * 0.5, 5, 2); ctx.fill();
        ctx.fillStyle = accent; roundRect(ctx, x + 10, y + h * 0.5, w * 0.6, 12, 4); ctx.fill();
      } else if (type === "rows") {
        for (let i = 0; i < 6; i++) {
          ctx.fillStyle = i % 2 ? "#ffffff" : "#f1ebdf"; roundRect(ctx, x, y + i * (h / 6), w, h / 6 - 4, 5); ctx.fill();
          ctx.fillStyle = "#cfc5b2"; roundRect(ctx, x + 10, y + i * (h / 6) + h / 12 - 5, w * 0.4, 6, 3); ctx.fill();
          ctx.fillStyle = i === 2 ? accent : "#3d3830"; roundRect(ctx, x + w - 60, y + i * (h / 6) + h / 12 - 5, 44, 6, 3); ctx.fill();
        }
      }
    }
  }

  if (!built) {
    ctx.fillStyle = "rgba(42,39,34,.55)";
    ctx.font = "italic 18px Fraunces, Georgia, serif";
    ctx.fillText(["fig. a — landing", "fig. b — catalogue", "fig. c — dashboard", "fig. d — detail", "fig. e — checkout"][variant % 5], 26, H - 16);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.anisotropy = 4;
  return texture;
}

function shadowTexture() {
  const c = document.createElement("canvas");
  c.width = 256;
  c.height = 320;
  const ctx = c.getContext("2d");
  ctx.filter = "blur(18px)";
  ctx.fillStyle = "rgba(30, 22, 10, 1)";
  ctx.fillRect(44, 44, 168, 232);
  return new THREE.CanvasTexture(c);
}

// ------------------------------------------------------------------ materials

const VERT = `
varying vec2 vUv;
varying vec3 vN;
void main() {
  vUv = uv;
  vN = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FRAG = `
uniform sampler2D mapA;
uniform sampler2D mapB;
uniform float morph;
uniform float opacity;
uniform vec3 accent;
varying vec2 vUv;
varying vec3 vN;
void main() {
  vec3 n = normalize(vN);
  float w = morph * 1.12 - 0.06;
  float y = 1.0 - vUv.y;
  float k = smoothstep(w + 0.015, w - 0.015, y);
  vec3 col = mix(texture2D(mapA, vUv).rgb, texture2D(mapB, vUv).rgb, k);
  float edge = (1.0 - smoothstep(0.0, 0.008, abs(y - w))) * step(0.002, morph) * step(morph, 0.998);
  col = mix(col, accent, edge);
  if (!gl_FrontFacing) { col = vec3(0.92, 0.89, 0.82); n = -n; }
  float d = max(dot(n, normalize(vec3(0.35, 0.55, 1.0))), 0.0);
  col *= 0.8 + 0.24 * d;
  gl_FragColor = vec4(col, opacity);
}`;

// ------------------------------------------------------------------ pencil

function makePencil() {
  const pencil = new THREE.Group();
  const add = (geometry, color, y, extra = {}) => {
    const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness: 0.55, flatShading: true, ...extra }));
    mesh.position.y = y;
    pencil.add(mesh);
    return mesh;
  };
  add(new THREE.CylinderGeometry(0.085, 0.085, 2.2, 6), 0xd9a21b, 0);
  add(new THREE.CylinderGeometry(0.092, 0.092, 0.22, 18), 0xbdb6a6, 1.21, { metalness: 0.85, roughness: 0.3, flatShading: false });
  add(new THREE.CylinderGeometry(0.086, 0.086, 0.18, 18), 0xd4776f, 1.41, { flatShading: false });
  add(new THREE.ConeGeometry(0.085, 0.36, 6), 0xe6c49a, -1.28).rotation.x = Math.PI;
  add(new THREE.ConeGeometry(0.03, 0.12, 6), 0x2b2b2b, -1.4, { metalness: 0.4 }).rotation.x = Math.PI;
  pencil.position.y = 1.46; // put the tip at the pivot's origin
  const pivot = new THREE.Group();
  pivot.add(pencil);
  return pivot;
}

// ------------------------------------------------------------------ mount / unmount

async function mount(canvas, state) {
  unmount();
  if (document.fonts && document.fonts.ready) await document.fonts.ready.catch(() => {});
  if (!canvas.isConnected) return;

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch (e) {
    canvas.style.display = "none"; // no WebGL: the page works without the scene
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  camera.position.set(0, 0, 11);
  scene.add(new THREE.AmbientLight(0xfff1dc, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 2.4);
  sun.position.set(3, 5, 6);
  scene.add(sun);

  const root = new THREE.Group();
  scene.add(root);

  const disposables = [];
  const shadowMap = shadowTexture();
  disposables.push(shadowMap);
  const sheetGeometry = new THREE.PlaneGeometry(2.4, 3.2);
  const shadowGeometry = new THREE.PlaneGeometry(3.0, 3.75);
  disposables.push(sheetGeometry, shadowGeometry);

  const rnd = seeded(42);
  const sheets = [];
  for (let i = 0; i < N; i++) {
    const mapA = drawSheet(i, false);
    const mapB = drawSheet(i, true);
    const material = new THREE.ShaderMaterial({
      uniforms: { mapA: { value: mapA }, mapB: { value: mapB }, morph: { value: 0 }, opacity: { value: 1 }, accent: { value: new THREE.Color(ACCENTS[i]) } },
      vertexShader: VERT,
      fragmentShader: FRAG,
      side: THREE.DoubleSide,
      transparent: true,
    });
    const group = new THREE.Group();
    const sheet = new THREE.Mesh(sheetGeometry, material);
    const shadowMaterial = new THREE.MeshBasicMaterial({ map: shadowMap, transparent: true, opacity: 0.28, depthWrite: false });
    const shadow = new THREE.Mesh(shadowGeometry, shadowMaterial);
    shadow.position.set(0.14, -0.18, -0.04);
    group.add(shadow, sheet);
    root.add(group);
    disposables.push(mapA, mapB, material, shadowMaterial);
    sheets.push({ group, material, shadowMaterial, jx: (rnd() - 0.5) * 0.16, jy: (rnd() - 0.5) * 0.12, jr: (rnd() - 0.5) * 0.08 });
  }

  const pencil = makePencil();
  root.add(pencil);
  pencil.traverse((o) => { if (o.material) { o.material.transparent = true; disposables.push(o.geometry, o.material); } });

  const pointer = { x: 0, y: 0, sx: 0, sy: 0 };
  const onPointer = (e) => {
    pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
  };
  let mobile = false;
  const onResize = () => {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    mobile = camera.aspect < 0.95;
  };
  window.addEventListener("pointermove", onPointer, { passive: true });
  window.addEventListener("resize", onResize);
  onResize();

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const start = performance.now();
  let raf = 0;

  const frame = (now) => {
    raf = requestAnimationFrame(frame);
    if (document.hidden) return;
    const s = state;
    canvas.style.opacity = String(clamp(s.fade, 0, 1) * (mobile ? 0.3 : 1));
    if (s.fade <= 0.01) return;
    const t = reduced ? 0 : (now - start) / 1000;
    pointer.sx += (pointer.x - pointer.sx) * 0.05;
    pointer.sy += (pointer.y - pointer.sy) * 0.05;

    root.position.set(mobile ? 0.85 : s.x, s.y + (mobile ? 0.9 : 0), 0);
    root.scale.setScalar(s.scale * (mobile ? 0.7 : 1));
    root.rotation.set(s.rotX + pointer.sy * 0.1, s.rotY + pointer.sx * 0.16, 0);

    const mid = (N - 1) / 2;
    sheets.forEach((sh, i) => {
      const o = i - mid;
      const lift = s.fan > 0.01 ? Math.max(0, 1 - Math.abs(s.active - i)) * s.fan : 0;
      const bob = Math.sin(t * 0.8 + i * 1.3) * 0.05;
      sh.group.position.set(
        sh.jx + o * 0.44 * s.spread + o * 0.66 * s.fan,
        sh.jy - o * 0.2 * s.spread - Math.abs(o) * 0.18 * s.fan + bob + lift * 0.3,
        -i * 0.16 - i * 0.32 * s.spread + lift * 1.1,
      );
      sh.group.rotation.set(0.02 * Math.sin(t * 0.6 + i), -o * 0.08 * s.fan, sh.jr - o * 0.06 * s.spread - o * 0.15 * s.fan);
      const m = clamp(s.active + 1 - i, 0, 1) * s.morph;
      sh.material.uniforms.morph.value = s.morph >= 0.999 ? 1 : m;
      sh.material.uniforms.opacity.value = s.fade;
      sh.shadowMaterial.opacity = 0.28 * s.fade;
    });

    // the pencil hovers over the front sheet and keeps "drawing" a small loop
    const front = sheets[0].group.position;
    const away = s.pencil;
    pencil.position.set(
      front.x + 0.45 + Math.sin(t * 1.7) * 0.22 + away * 3.2,
      front.y - 0.25 + Math.sin(t * 3.4) * 0.12 + away * 3.6,
      front.z + 0.06 + Math.abs(Math.sin(t * 1.7)) * 0.04 + away * 2.0,
    );
    pencil.rotation.set(0.55 + pointer.sy * 0.05, 0.25, -0.6 + Math.sin(t * 1.7) * 0.05 - away * 0.6);
    pencil.traverse((o) => { if (o.material) o.material.opacity = s.fade * (1 - away * 0.9); });

    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);

  current = {
    stop() {
      cancelAnimationFrame(raf);
      window.removeEventListener("pointermove", onPointer);
      window.removeEventListener("resize", onResize);
      disposables.forEach((d) => d.dispose());
      renderer.dispose();
    },
  };
}

function unmount() {
  if (current) current.stop();
  current = null;
}

window.NSScene = { mount, unmount };
window.dispatchEvent(new Event("nsscene:ready"));
