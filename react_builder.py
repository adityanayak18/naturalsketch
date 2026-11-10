"""React projects: plan -> generate files -> assemble a real Vite + React + Tailwind project.

A React project is a set of files (the same storage as HTML sites). The AI writes the layout,
pages and components; everything structural (routing, the data store, Tailwind/Vite config,
package.json) is written by code here so it is always valid. The preview bundles the files in
the browser (see `runner_html`), and the ZIP export is a normal `npm install && npm run dev` project.
"""

import json
import logging
import posixpath
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import backends
import llm
import projects
import refsite
from builder import (
    COLOR_KEYS,
    DEFAULT_THEME,
    _features_text,
    _font,
    _hex,
    _icon,
    _images_for,
    _luminance,
    _readable_theme,
    _slug,
    _text_list,
    readable_on,
)

logger = logging.getLogger("naturalsketch.react")

PARALLEL_FILES = 2  # more trips Gemini's free rate limit and pushes files to weaker models
MAX_ROUTES = 8
MAX_COMPONENTS = 12

# Libraries generated code may import, pinned so the preview and the exported project match.
# The preview loads them from esm.sh; react, react-dom and three are shared singletons.
PACKAGES = {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "react-router-dom": "6.26.2",
    "three": "0.160.0",
    "@react-three/fiber": "8.17.10",
    "@react-three/drei": "9.114.0",
    "framer-motion": "11.11.9",
    "gsap": "3.12.5",
    "lucide-react": "0.460.0",
    "recharts": "2.13.0",
    "clsx": "2.1.1",
    "date-fns": "3.6.0",
    "lenis": "1.1.13",
    "canvas-confetti": "1.9.3",
}
ALWAYS = ["react", "react-dom", "react-router-dom"]
SINGLETONS = ["react", "react-dom", "three"]
DEV_PACKAGES = {
    "vite": "5.4.10",
    "@vitejs/plugin-react": "4.3.3",
    "tailwindcss": "3.4.14",
    "postcss": "8.4.47",
    "autoprefixer": "10.4.20",
}
ESBUILD_VERSION = "0.24.0"
TAILWIND_PLAY = "https://cdn.tailwindcss.com/3.4.5"

LIBRARY_GUIDE = """- react-router-dom: Link, NavLink, useNavigate, useParams, Outlet (routing is already set up; use these).
- three, @react-three/fiber (Canvas, useFrame, useThree), @react-three/drei (OrbitControls, Float, Text, Environment, MeshDistortMaterial, Stars, Sparkles, ScrollControls, useScroll, ContactShadows, RoundedBox, Html...): 3D scenes. Always size the <Canvas> parent (e.g. className="h-[70vh] w-full"). Use only built-in geometries and procedural materials: no external .glb/.hdr/texture files. For Environment use a preset (e.g. preset="city").
- framer-motion (motion, AnimatePresence, useScroll, useTransform, useInView): animation, scroll effects, page transitions.
- gsap (and gsap/ScrollTrigger): timeline and scroll animation; register plugins inside useEffect and clean up.
- lenis: smooth scrolling (create in a useEffect in the layout and destroy on unmount).
- lucide-react: icons, e.g. import { ArrowRight, Github } from "lucide-react".
- recharts: charts. date-fns: dates. clsx: class names. canvas-confetti: celebrations."""

LIB_DOC = """Shared helpers in src/lib/store.js (persist to the browser, re-render automatically):
- import { useCollection, useStore, useAuth, db } from "../lib/store.js"  (from pages and components; adjust the relative path)
- const [items, api] = useCollection("projects")  -> items: array of records seeded from the app data. api.add(obj) returns the saved record with a new id; api.update(id, patch); api.remove(id); api.get(id).
- const [value, setValue] = useStore("cart", [])  -> persistent shared state (cart, favourites, settings, form drafts...). setValue(v) or setValue(prev => next).
- const { user, login, logout } = useAuth()  -> mock auth: login({ name, email }).
- db.all(name), db.get(name, id) for reading outside components.
Data collections (seeded): use them instead of hard-coding records that exist in the data."""

PLAN_SYSTEM = """You are a senior creative technologist and product architect. Turn a website or app idea (the user's description, feature list, and when available an analysis of their sketches/screenshots and notes about reference sites) into a plan for a real React project (Vite + React 18 + Tailwind CSS + react-router), which can include 3D (three.js / react-three-fiber), scroll animation and motion. Return ONLY a JSON object:

{
  "app_name": "short brand name",
  "tagline": "one line",
  "style": "2-4 sentences of visual direction: mood, layout ideas, motion and 3D ideas, typography",
  "theme": {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "background": "#hex", "surface": "#hex",
            "foreground": "#hex", "muted": "#hex", "border": "#hex", "font_heading": "Google Font", "font_body": "Google Font"},
  "routes": [
    {"slug": "index", "title": "Home", "nav": true, "param": null, "purpose": "what the page is for",
     "sections": ["ordered sections, with specifics"], "features": ["features from the list this page implements"],
     "uses": ["ComponentName"], "image": null}
  ],
  "components": [
    {"name": "HeroScene", "purpose": "what it renders and how it behaves", "props": "{ title: string }", "libraries": ["@react-three/fiber", "@react-three/drei"]}
  ],
  "layout": "what the shared Layout renders around every page (navigation style, footer, background effects, smooth scroll)",
  "packages": ["three", "@react-three/fiber", "@react-three/drei", "framer-motion"],
  "data": {"collection_name": [{"id": "1", "field": "value"}]}
}

Rules:
- 2 to 6 routes; the first has slug "index". Slugs are lowercase-with-hyphens. A detail page (e.g. a single project) sets "param": "id", "nav": false, and reads it with useParams.
- 3 to 10 reusable components, PascalCase names, each used by at least one route. Put 3D scenes, animated sections, cards and forms in components.
- Every feature from the list is covered by a route or component.
- packages: only from this list: react-router-dom, three, @react-three/fiber, @react-three/drei, framer-motion, gsap, lenis, lucide-react, recharts, clsx, date-fns, canvas-confetti. Use 3D only when it suits the idea (portfolios, product showcases, creative sites), not by default for dashboards.
- data: 1 to 4 collections with 4 to 8 realistic, specific records (projects, products, posts, testimonials...), string ids, image URLs like https://picsum.photos/seed/<word>/800/600.
- theme: follow screenshots/references when given; otherwise a distinctive palette that suits the idea, readable text. Real Google Fonts only."""

FILE_SYSTEM = f"""You are a senior React engineer and creative frontend developer. You write ONE file of a real Vite + React 18 + Tailwind CSS project. The project must look like a polished, award-quality site and actually work.

Output: ONLY the complete file content (JSX). No markdown fences, no explanations.

Rules:
- Default-export the component named in the request. Function components and hooks only.
- Import only: react, the libraries listed below, and the project's own files listed in the request (relative paths with the .jsx/.js extension, e.g. "../components/HeroScene.jsx"). Nothing else: no CSS imports, no images, no other npm packages, no fetch to external APIs.
- Style with Tailwind classes. Theme colours: bg-primary text-primary-foreground, bg-secondary, bg-accent, bg-background, bg-surface, text-foreground, text-muted, border-border (with opacity modifiers like bg-primary/10). Headings use font-heading. Never hard-code hex colours in classNames (inline 3D material colours may use the theme hex values given).
- Fully responsive (360px to desktop), accessible (alt text, labels, focus states), realistic specific copy (no lorem ipsum).
- Images: https://picsum.photos/seed/<word>/<w>/<h> or URLs from the data.
- Links between pages: <Link to="/slug"> (or "/" for home; detail pages "/slug/<id>").

Libraries available:
{LIBRARY_GUIDE}

{LIB_DOC}"""

EDIT_SYSTEM = FILE_SYSTEM + """

You are editing an existing file. Apply the requested change, keep everything else that works, and return the complete updated file."""

ROUTER_SYSTEM = """You are the assistant inside an AI app builder. The user is iterating on a generated React project (Vite + React + Tailwind + react-router, optional three.js/framer-motion). Decide what to change and return ONLY a JSON object:
{
  "reply": "1-3 friendly sentences telling the user what you are changing, or answering their question",
  "app_name": null,
  "theme": null,
  "add_routes": [],
  "add_components": [],
  "edit_files": [],
  "remove_routes": [],
  "packages": [],
  "data_additions": {}
}
- app_name: a new name, or null. theme: only the fields to change (primary, secondary, accent, background, surface, foreground, muted, border, font_heading, font_body), or null. Theme changes apply everywhere automatically.
- add_routes: [{"slug", "title", "nav", "param": null or "id", "purpose", "sections", "features", "uses": ["ComponentName"]}].
- add_components: [{"name": "PascalCase", "purpose", "props", "libraries"}].
- edit_files: [{"path": "an existing file path from the list", "instruction": "precise change for the developer"}]. To make a new component appear, also edit the file that should render it.
- remove_routes: slugs to delete (never "index").
- packages: libraries to add, only from: react-router-dom, three, @react-three/fiber, @react-three/drei, framer-motion, gsap, lenis, lucide-react, recharts, clsx, date-fns, canvas-confetti.
- data_additions: {"collection": [new records with string ids]}.
Rules: only touch files that need to change. If the user reports an error, find the file(s) it comes from (the error usually names the file or component) and edit them with a precise fix instruction. If the user only asks a question, answer in "reply" and change nothing."""


# ---------------------------------------------------------------- plan

def _pascal(value: str, default: str = "Section") -> str:
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    name = "".join(w[:1].upper() + w[1:] for w in words)
    if not name or not name[0].isalpha():
        name = default + name
    return name[:40]


def _packages(values) -> list[str]:
    wanted = [str(v).strip() for v in values or [] if str(v).strip() in PACKAGES]
    if any(p in wanted for p in ("@react-three/fiber", "@react-three/drei")):
        wanted += ["three", "@react-three/fiber"]
    return sorted(set(wanted) | set(ALWAYS))


ACCOUNT_COLLECTIONS = {"users", "user", "accounts", "account", "logins", "auth", "credentials"}
SENSITIVE_FIELD = re.compile(r"pass(word|wd)?|secret|token|salt|hash", re.IGNORECASE)


def normalize_plan(raw: dict, image_count: int) -> dict:
    theme_in = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {key: _hex(theme_in.get(key), DEFAULT_THEME[key]) for key in COLOR_KEYS}
    _readable_theme(theme)
    theme["font_heading"] = _font(theme_in.get("font_heading"), DEFAULT_THEME["font_heading"])
    theme["font_body"] = _font(theme_in.get("font_body"), DEFAULT_THEME["font_body"])
    theme["mode"] = "dark" if _luminance(theme["background"]) < 0.2 else "light"
    theme["primary_foreground"] = readable_on(theme["primary"])

    components, names = [], set()
    for item in raw.get("components") or []:
        if not isinstance(item, dict):
            continue
        name = _pascal(item.get("name"), "Block")
        if name in names or name in ("Layout", "App"):
            continue
        names.add(name)
        components.append({
            "name": name,
            "file": f"src/components/{name}.jsx",
            "purpose": str(item.get("purpose") or "")[:600],
            "props": str(item.get("props") or "{}")[:200],
            "libraries": [p for p in _text_list(item.get("libraries"), 8) if p in PACKAGES],
        })
    components = components[:MAX_COMPONENTS]
    names = {c["name"] for c in components}

    routes, slugs = [], set()
    for item in raw.get("routes") or raw.get("pages") or []:
        if not isinstance(item, dict):
            continue
        slug = _slug(item.get("slug") or item.get("title"))
        if slug in slugs:
            continue
        slugs.add(slug)
        param = "id" if item.get("param") else None
        image = item.get("image")
        routes.append({
            "slug": slug,
            "title": str(item.get("title") or slug.replace("-", " ").title())[:40],
            "nav": item.get("nav") is not False and not param,
            "param": param,
            "component": _pascal(item.get("title") or slug, "Page") + "Page",
            "purpose": str(item.get("purpose") or "")[:600],
            "sections": _text_list(item.get("sections")),
            "features": _text_list(item.get("features")),
            "uses": [n for n in (_pascal(u) for u in item.get("uses") or []) if n in names],
            "image": image if isinstance(image, int) and 0 <= image < image_count else None,
        })
    if not routes:
        raise ValueError("the plan has no routes")
    if "index" not in slugs:
        routes[0]["slug"] = "index"
    routes.sort(key=lambda r: r["slug"] != "index")
    routes = routes[:MAX_ROUTES]
    seen_components = set()
    for route in routes:  # page component names must be unique
        while route["component"] in seen_components or route["component"] in names:
            route["component"] += "View"
        seen_components.add(route["component"])
        route["file"] = f"src/pages/{route['component']}.jsx"

    data = {}
    raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    full_stack = raw.get("backend") in backends.LANGUAGES
    for name, records in list(raw_data.items())[:6]:
        key = re.sub(r"[^a-z0-9_]", "_", str(name).lower()).strip("_")
        if full_stack and key in ACCOUNT_COLLECTIONS:
            continue  # every collection becomes a public API resource; accounts are handled by the backend's sign-in
        if key and isinstance(records, list):
            data[key] = [
                {**{k: v for k, v in r.items() if not SENSITIVE_FIELD.search(str(k))}, "id": str(r.get("id") or i + 1)}
                for i, r in enumerate(x for x in records[:40] if isinstance(x, dict))
            ]

    packages = _packages(list(raw.get("packages") or []) + [p for c in components for p in c["libraries"]])
    return {
        "kind": "react",
        # Full-stack projects: the backend language; the frontend then loads its data through the API.
        "backend": raw.get("backend") if raw.get("backend") in backends.LANGUAGES else None,
        "app_name": str(raw.get("app_name") or "My Site").strip()[:40],
        "tagline": str(raw.get("tagline") or "")[:140],
        "style": str(raw.get("style") or "")[:800],
        "layout": str(raw.get("layout") or "Top navigation with links to the main pages, and a footer.")[:600],
        "theme": theme,
        "routes": routes,
        "components": components,
        "packages": packages,
        "data": data,
    }


# ---------------------------------------------------------------- fixed project files

def tailwind_theme(theme: dict) -> dict:
    stack = ["ui-sans-serif", "system-ui", "sans-serif"]
    colors = {key: theme[key] for key in COLOR_KEYS}
    colors["primary"] = {"DEFAULT": theme["primary"], "foreground": theme["primary_foreground"]}
    return {"extend": {
        "colors": colors,
        "fontFamily": {"heading": [theme["font_heading"], *stack], "body": [theme["font_body"], *stack]},
    }}


def _fonts_href(theme: dict) -> str:
    families = "&".join(
        f"family={font.replace(' ', '+')}:wght@300;400;500;600;700;800" for font in dict.fromkeys([theme["font_heading"], theme["font_body"]])
    )
    return f"https://fonts.googleapis.com/css2?{families}&display=swap"


def route_path(route: dict) -> str:
    base = "" if route["slug"] == "index" else route["slug"]
    return f"{base}/:id".lstrip("/") if route["param"] else base


STORE_JS = """// Tiny persistent store for this app: data collections, shared state and mock auth.
// Everything is kept in the browser's localStorage, so the app works without a backend.
import { useSyncExternalStore } from "react";
import seed from "./data.json";

const PREFIX = "ns-app:";
const listeners = new Set();
const cache = new Map();

function read(key, fallback) {
  if (cache.has(key)) return cache.get(key);
  let value = fallback;
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (raw != null) value = JSON.parse(raw);
  } catch (e) {}
  cache.set(key, value);
  return value;
}

function write(key, value) {
  cache.set(key, value);
  try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch (e) {}
  listeners.forEach((fn) => fn());
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

export const db = {
  all: (name) => read("db:" + name, seed[name] || []),
  get: (name, id) => db.all(name).find((r) => String(r.id) === String(id)) || null,
  add(name, record) {
    const saved = { ...record, id: record.id != null ? String(record.id) : newId() };
    write("db:" + name, [...db.all(name), saved]);
    return saved;
  },
  update(name, id, patch) {
    write("db:" + name, db.all(name).map((r) => (String(r.id) === String(id) ? { ...r, ...patch } : r)));
  },
  remove(name, id) {
    write("db:" + name, db.all(name).filter((r) => String(r.id) !== String(id)));
  },
};

export function useCollection(name) {
  const items = useSyncExternalStore(subscribe, () => db.all(name));
  const api = {
    add: (record) => db.add(name, record),
    update: (id, patch) => db.update(name, id, patch),
    remove: (id) => db.remove(name, id),
    get: (id) => db.get(name, id),
  };
  return [items, api];
}

export function useStore(key, fallback) {
  const value = useSyncExternalStore(subscribe, () => read("store:" + key, fallback));
  const set = (next) => write("store:" + key, typeof next === "function" ? next(read("store:" + key, fallback)) : next);
  return [value, set];
}

export function useAuth() {
  const [user, setUser] = useStore("auth:user", null);
  return {
    user,
    login: (details) => setUser({ name: details.name || (details.email || "").split("@")[0], ...details }),
    logout: () => setUser(null),
  };
}
"""

MAIN_JSX = """import React from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App.jsx";
import "./index.css";

// Shows a readable message instead of a blank page if a component crashes.
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    if (window.__nsReport) window.__nsReport(error, info && info.componentStack);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, fontFamily: "system-ui, sans-serif" }}>
          <h1 style={{ fontSize: 20, marginBottom: 8 }}>Something went wrong on this page.</h1>
          <pre style={{ whiteSpace: "pre-wrap", color: "#b91c1c" }}>{String(this.state.error.message || this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary>
      <HashRouter>
        <App />
      </HashRouter>
    </ErrorBoundary>
  </React.StrictMode>,
);
"""


def app_jsx(plan: dict) -> str:
    imports = "\n".join(f'import {r["component"]} from "./pages/{r["component"]}.jsx";' for r in plan["routes"])
    home = plan["routes"][0]["component"]
    routes = "\n".join(
        f'        <Route index element={{<{r["component"]} />}} />' if r["slug"] == "index"
        else f'        <Route path="{route_path(r)}" element={{<{r["component"]} />}} />'
        for r in plan["routes"]
    )
    return f"""import {{ Routes, Route }} from "react-router-dom";
import Layout from "./components/Layout.jsx";
{imports}

export default function App() {{
  return (
    <Routes>
      <Route element={{<Layout />}}>
{routes}
        <Route path="*" element={{<{home} />}} />
      </Route>
    </Routes>
  );
}}
"""


def index_css(theme: dict) -> str:
    return f"""@tailwind base;
@tailwind components;
@tailwind utilities;

html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  background: {theme['background']};
  color: {theme['foreground']};
  font-family: '{theme['font_body']}', ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}}
h1, h2, h3, h4, h5, h6 {{ font-family: '{theme['font_heading']}', ui-sans-serif, system-ui, sans-serif; }}
"""


def _slugify_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "app"


def scaffold(plan: dict) -> dict[str, str]:
    """The files written by code, not the AI. Regenerated on every version."""
    theme = plan["theme"]
    deps = {name: f"^{PACKAGES[name]}" for name in plan["packages"]}
    package = {
        "name": _slugify_name(plan["app_name"]),
        "private": True,
        "version": "1.0.0",
        "type": "module",
        "scripts": {"dev": "vite", "build": "vite build", "preview": "vite preview"},
        "dependencies": deps,
        "devDependencies": {name: f"^{version}" for name, version in DEV_PACKAGES.items()},
    }
    title = plan["app_name"].replace("<", "").replace(">", "")
    return {
        "package.json": json.dumps(package, indent=2) + "\n",
        "index.html": f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{title}</title>
    <meta name="description" content="{plan['tagline'].replace('"', "'")}" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="stylesheet" href="{_fonts_href(theme)}" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
""",
        "vite.config.js": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n  // 3D and animation libraries are large; that is expected.\n  build: { chunkSizeWarningLimit: 2500 },\n});\n',
        "tailwind.config.js": "/** @type {import('tailwindcss').Config} */\nexport default "
        + json.dumps({"content": ["./index.html", "./src/**/*.{js,jsx}"], "theme": tailwind_theme(theme), "plugins": []}, indent=2)
        + ";\n",
        "postcss.config.js": "export default {\n  plugins: {\n    tailwindcss: {},\n    autoprefixer: {},\n  },\n};\n",
        "src/main.jsx": MAIN_JSX,
        "src/App.jsx": app_jsx(plan),
        "src/index.css": index_css(theme),
        "src/lib/store.js": STORE_JS,
        "src/lib/data.json": json.dumps(plan["data"], ensure_ascii=False, indent=2) + "\n",
        ".gitignore": "node_modules\ndist\n.DS_Store\n",
        "README.md": f"""# {plan['app_name']}

{plan['tagline']}

A React project (Vite, React 18, Tailwind CSS, React Router{', three.js' if 'three' in plan['packages'] else ''}{', Framer Motion' if 'framer-motion' in plan['packages'] else ''}).

## Run it

```bash
npm install
npm run dev
```

Then open the address Vite prints (usually http://localhost:5173).

## Build for production

```bash
npm run build
```

The site is written to `dist/`. Upload that folder to any static host (Netlify, Vercel, GitHub Pages...).

## Where things are

- `src/pages/`: one component per page; routes are in `src/App.jsx`
- `src/components/`: the shared layout and reusable sections
- `src/lib/data.json`: the sample data; `src/lib/store.js` keeps changes in the browser (no backend needed)
""",
    }


def generated_files(plan: dict) -> list[dict]:
    """The files the AI writes: the layout, every component, every page."""
    files = [{
        "path": "src/components/Layout.jsx", "name": "Layout", "kind": "layout",
        "purpose": plan["layout"] + " Renders <Outlet /> from react-router-dom for the current page.",
        "props": "{}", "image": None,
    }]
    files += [{"path": c["file"], "name": c["name"], "kind": "component", "purpose": c["purpose"], "props": c["props"], "image": None}
              for c in plan["components"]]
    files += [{"path": r["file"], "name": r["component"], "kind": "page", "route": r, "purpose": r["purpose"], "props": "{}",
               "image": r["image"]} for r in plan["routes"]]
    return files


# ---------------------------------------------------------------- import checking

IMPORT_RE = re.compile(
    r"""(?:^|[;\n])\s*(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]|\bimport\(\s*['"]([^'"]+)['"]\s*\)""",
    re.MULTILINE,
)


def package_of(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def imports_of(source: str) -> list[str]:
    return [a or b for a, b in IMPORT_RE.findall(source)]


def resolve_local(importer: str, spec: str, paths: set[str]) -> str | None:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    for candidate in (base, base + ".jsx", base + ".js", base + "/index.jsx", base + "/index.js"):
        if candidate in paths:
            return candidate
    return None


def check_imports(path: str, source: str, paths: set[str], packages: list[str]) -> None:
    """Reject a generated file that imports something the project doesn't have."""
    for spec in imports_of(source):
        if spec.startswith("."):
            if spec.endswith(".css"):
                raise ValueError(f"imports a CSS file ({spec})")
            if not resolve_local(path, spec, paths):
                raise ValueError(f"imports a file that doesn't exist ({spec})")
        elif package_of(spec) not in PACKAGES:
            raise ValueError(f"imports a library that isn't available ({spec})")


MISUSE = [
    (re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*useCollection\("),
     "useCollection returns [items, api]; destructure it: const [items, api] = useCollection(...)"),
    (re.compile(r"\b(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=\s*useStore\("),
     "useStore returns [value, setValue]; destructure it"),
    (re.compile(r"\b(?:BrowserRouter|HashRouter|MemoryRouter|RouterProvider|createBrowserRouter)\b"),
     "creates a second router; routing is already set up in App.jsx"),
]


def check_conventions(spec: dict, source: str) -> None:
    """Reject the mistakes weaker models make that don't crash but break the page."""
    if spec["kind"] == "page" and re.search(r"""from\s+['"][^'"]*components/Layout(?:\.jsx)?['"]|<Layout\b""", source):
        raise ValueError("a page wraps itself in Layout, which already wraps every page")
    for pattern, problem in MISUSE:
        if pattern.search(source):
            raise ValueError(problem)


def clean_source(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    return text.strip() + "\n"


def placeholder(name: str, purpose: str) -> str:
    note = json.dumps(purpose[:160] or "This section is still being built.")
    return f"""export default function {name}() {{
  return (
    <section className="mx-auto max-w-5xl px-6 py-24 text-center">
      <h2 className="font-heading text-3xl text-foreground">{name}</h2>
      <p className="mt-3 text-muted">{{{note}}}</p>
    </section>
  );
}}
"""


# ---------------------------------------------------------------- prompts

def _contract(plan: dict) -> str:
    lines = ["- src/components/Layout.jsx: default export Layout (wraps every page, renders <Outlet />)"]
    lines += [f"- {c['file']}: default export {c['name']}, props {c['props']}: {c['purpose']}" for c in plan["components"]]
    lines += [
        f"- {r['file']}: default export {r['component']}, page at \"/{route_path(r)}\": {r['title']}"
        for r in plan["routes"]
    ]
    lines += ["- src/lib/store.js: useCollection, useStore, useAuth, db (see helpers)"]
    return "\n".join(lines)


def _data_summary(plan: dict) -> str:
    out = []
    for name, records in plan["data"].items():
        fields = sorted({k for r in records for k in r})
        out.append(f"- {name} ({len(records)} records; fields: {', '.join(fields)}). Sample: {json.dumps(records[:2], ensure_ascii=False)[:700]}")
    return "\n".join(out) or "(no collections)"


def backend_note(plan: dict) -> str:
    if not plan.get("backend"):
        return ""
    return (
        f"BACKEND: this project has a real REST API ({backends.LANGUAGES[plan['backend']]['label']}), and the helpers in "
        "src/lib/store.js load and save through it. Collections arrive a moment after the first render, so `items` can be "
        "empty at first: show loading or empty states and never assume a record exists. Signing in is "
        "`await login({ email, password, name })`, which throws an Error with a readable message if the password is wrong "
        "(show it). Never call fetch yourself: use the store helpers.\n\n"
    )


def file_prompt(project: dict, plan: dict, spec: dict, written: dict[str, str] | None = None) -> str:
    theme = plan["theme"]
    nav = ", ".join(f"{r['title']} (\"/{'' if r['slug'] == 'index' else r['slug']}\")" for r in plan["routes"] if r["nav"])
    task = f"WRITE: {spec['path']}  (default export {spec['name']}, props {spec['props']})\nPURPOSE: {spec['purpose']}"
    if spec["kind"] == "page":
        route = spec["route"]
        task += (
            f"\nPAGE: {route['title']} at \"/{route_path(route)}\"" + (" (read the id with useParams)" if route["param"] else "")
            + "\nSECTIONS, IN ORDER:\n" + "\n".join(f"- {s}" for s in route["sections"])
            + ("\nFEATURES TO IMPLEMENT:\n" + "\n".join(f"- {f}" for f in route["features"]) if route["features"] else "")
            + ("\nUSE THESE COMPONENTS: " + ", ".join(route["uses"]) if route["uses"] else "")
            + "\nThe Layout already renders the navigation and footer: do not repeat them."
        )
        # Pages are written after their components and see their actual code, so they compose
        # them instead of re-creating what they already show (e.g. a hero's headline twice).
        shown = []
        for name in route["uses"]:
            source = (written or {}).get(f"src/components/{name}.jsx")
            if source:
                shown.append(f"--- src/components/{name}.jsx ---\n{source[:3500]}")
        if shown:
            task += (
                "\n\nTHE COMPONENTS THIS PAGE USES, AS ALREADY WRITTEN (read them before you start):\n" + "\n".join(shown)
                + "\nRender them with the props they accept. Do NOT repeat anything they already render "
                "(headlines, copy, buttons, 3D scenes): add only what is missing around them."
            )
    elif spec["kind"] == "component":
        task += (
            "\nRender only what this purpose describes: the page decides the surrounding layout and copy. "
            "If it is a 3D scene, render just the scene filling its parent (no headlines or page copy) "
            "unless the purpose explicitly asks for text."
        )
    elif spec["kind"] == "layout":
        task += f"\nNAVIGATION LINKS: {nav}\nInclude a mobile menu. Render the current page with <Outlet />."
    return f"""PROJECT: {plan['app_name']}: {plan['tagline']}
USER'S IDEA: {project['prompt'] or '(see the plan)'}
REQUESTED FEATURES:
{_features_text(project)}
VISUAL DIRECTION: {plan['style'] or 'polished, modern'}
THEME HEX (for 3D materials or canvas only): {json.dumps({k: theme[k] for k in COLOR_KEYS})}; fonts: {theme['font_heading']} / {theme['font_body']}; {theme['mode']} mode.
LIBRARIES INSTALLED: {', '.join(plan['packages'])}

PROJECT FILES (import from these only, with relative paths):
{_contract(plan)}

DATA COLLECTIONS:
{_data_summary(plan)}

{backend_note(plan)}{task}"""


def router_prompt(project: dict, plan: dict, message: str) -> str:
    files = "\n".join(f"- {f['path']}: {f['name']}: {f['purpose'][:160]}" for f in generated_files(plan))
    history = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in project["chat"][-8:])
    return f"""PROJECT: {plan['app_name']}: {plan['tagline']}
STYLE: {plan['style']}
THEME: {json.dumps({k: plan['theme'][k] for k in COLOR_KEYS + ['font_heading', 'font_body']})}
ROUTES: {', '.join(f"{r['slug']} ({r['title']})" for r in plan['routes'])}
LIBRARIES: {', '.join(plan['packages'])}
FILES YOU CAN EDIT:
{files}
DATA COLLECTIONS: {', '.join(plan['data']) or '(none)'}

RECENT CONVERSATION:
{history or '(none)'}

USER MESSAGE: {message}"""


# ---------------------------------------------------------------- generation

def _generate(
    session, plan: dict, jobs: list[tuple[dict, str | None, str | None]], existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """jobs: (file spec, change instruction or None, current source or None).

    Two passes, each in parallel: the layout and components first, then the pages, which are
    shown the components they use (`existing` holds unchanged files from the current version).
    """
    project = session.project
    paths = {f["path"] for f in generated_files(plan)} | {"src/lib/store.js", "src/lib/data.json"}
    written = dict(existing or {})

    def one(spec, instruction, current):
        label = spec["name"]
        session.log(f"{'Updating' if current else 'Writing'} {spec['path']}", "step")
        prompt = file_prompt(project, plan, spec, written)
        images = []
        if current:
            prompt += f"\n\nCURRENT FILE:\n{current}\n\nCHANGE REQUEST: {instruction}"
        elif spec.get("image") is not None:
            images, labels = _images_for(project, [spec["image"]])
            prompt += "\n\nATTACHED (follow its layout): " + "; ".join(labels)

        def validate(text):
            source = clean_source(text)
            if "export default" not in source:
                raise ValueError("no default export")
            check_imports(spec["path"], source, paths, plan["packages"])
            check_conventions(spec, source)
            return source

        try:
            source, model = llm.complete(
                "code", EDIT_SYSTEM if current else FILE_SYSTEM, prompt, images=images, max_tokens=16000,
                validate=validate, own=session.own, on_fallback=session.fallback(label),
            )
            session.log(f"{spec['path']} ready", "done", model)
            return source
        except llm.LLMError:
            session.log(f"{spec['path']}: every provider failed, kept a placeholder", "error")
            return current or placeholder(spec["name"], spec["purpose"])

    results = {}
    for batch in ([j for j in jobs if j[0]["kind"] != "page"], [j for j in jobs if j[0]["kind"] == "page"]):
        with ThreadPoolExecutor(max_workers=PARALLEL_FILES) as pool:
            futures = {pool.submit(one, *job): job[0]["path"] for job in batch}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        written.update(results)
    return results


def assemble(plan: dict, sources: dict[str, str]) -> dict[str, str]:
    # The dependencies follow what the files actually import.
    used = {package_of(s) for path, source in sources.items() for s in imports_of(source or "") if not s.startswith(".")}
    plan["packages"] = _packages(list(plan["packages"]) + [p for p in used if p in PACKAGES])
    files = scaffold(plan)
    for spec in generated_files(plan):
        files[spec["path"]] = sources.get(spec["path"]) or placeholder(spec["name"], spec["purpose"])
    if plan.get("backend"):
        # Full-stack: the data layer talks to the API, and the backend and project files are added.
        # Stored with the frontend at the top level (so the preview works unchanged); the ZIP export
        # arranges them as frontend/ and backend/.
        files["src/lib/store.js"] = backends.STORE_API_JS
        files["src/lib/config.js"] = backends.CONFIG_JS
        files[".env.example"] = "# Where the backend API runs\nVITE_API_URL=http://localhost:8000\n"
        files.update(backends.backend_files(plan, plan["backend"]))
        files["README.md"] = backends.root_readme(plan, plan["backend"])
        files["docker-compose.yml"] = backends.COMPOSE
    files["_src/plan.json"] = json.dumps(plan, ensure_ascii=False, indent=1)
    return files


def page_slugs(plan: dict) -> list[str]:
    """What the studio shows as page tabs: routes you can open without an id."""
    return [r["slug"] for r in plan["routes"] if not r["param"]]


def build(session, brief_text: str, image_count: int) -> None:
    """Plan and write a new React project. Called from builder._build after image analysis."""
    project = session.project
    session.log("Planning routes, components, libraries and data", "step")
    prompt = (
        f"USER'S DESCRIPTION: {project['prompt'] or '(see the image analysis)'}\n"
        f"REQUIRED FEATURES:\n{_features_text(project)}\n"
        f"UPLOADED IMAGES: {image_count}\n"
        f"IMAGE ANALYSIS: {brief_text}\n"
        f"REFERENCE SITES:\n{refsite.describe(project.get('refs'))}"
    )
    backend = project.get("backend") if project.get("kind") == "fullstack" else None
    if backend:
        prompt += (
            f"\n\nFULL-STACK PROJECT with a {backends.LANGUAGES[backend]['label']} backend. Every data collection becomes "
            "a REST resource that the frontend loads and saves through the API, so design a proper data model: "
            "2 to 5 collections, the same fields with the same types in every record of a collection, ids as strings, "
            "relations as '<thing>Id' fields. Plan data-driven pages: lists with search and filters, detail pages, "
            "forms to create and edit records, and a dashboard or summary where it fits. Use 3D only if the idea asks for it. "
            "Do NOT add a users, accounts or logins collection and never put passwords in the data: accounts and sign-in "
            "are built into the backend (the frontend uses useAuth)."
        )
    plan, model = llm.complete(
        "plan", PLAN_SYSTEM, prompt, json_mode=True, max_tokens=12000,
        validate=lambda text: normalize_plan({**llm.parse_json(text), "backend": backend}, image_count),
        own=session.own, on_fallback=session.fallback("Planning"),
    )
    session.update(name=plan["app_name"])
    session.log(
        f"Planned {plan['app_name']}: {len(plan['routes'])} pages, {len(plan['components'])} components, "
        f"libraries: {', '.join(p for p in plan['packages'] if p not in ALWAYS) or 'React only'}",
        "done", model,
    )
    sources = _generate(session, plan, [(spec, None, None) for spec in generated_files(plan)])
    version = projects.write_version(session.pid, assemble(plan, sources), "Initial build", pages=page_slugs(plan))
    session.log(f"Version {version} is live", "done")
    extra = ""
    if plan.get("backend"):
        extra = (
            f" It comes with a {backends.LANGUAGES[plan['backend']]['label']} backend: a REST API for "
            f"{', '.join(plan['data']) or 'your data'} with sign-in. The preview runs the frontend on sample data; "
            "export the ZIP to run the frontend and backend together (the README has the steps)."
        )
    session.say(
        f"I built {plan['app_name']} as a React project with {len(plan['routes'])} pages "
        f"({', '.join(r['title'] for r in plan['routes'])}) and {len(plan['components'])} components.{extra} "
        "Explore the preview, open the code view to see the files, then tell me what to change."
    )


def chat(session, message: str, files: dict[str, str]) -> None:
    """Apply a chat request to an existing React project."""
    project = session.project
    plan = json.loads(files["_src/plan.json"])
    session.log("Working out what to change", "step")
    decision, model = llm.complete(
        "chat", ROUTER_SYSTEM, router_prompt(project, plan, message), json_mode=True, max_tokens=4000,
        validate=llm.parse_json, own=session.own, on_fallback=session.fallback("Chat"),
    )
    session.say(str(decision.get("reply") or "On it."))

    raw = json.loads(json.dumps(plan))
    notes = []
    if decision.get("app_name"):
        raw["app_name"] = decision["app_name"]
        notes.append("renamed the project")
    if isinstance(decision.get("theme"), dict) and decision["theme"]:
        raw["theme"].update(decision["theme"])
        notes.append("updated the theme")
    removed = {_slug(s) for s in decision.get("remove_routes") or []} - {"index"}
    if removed & {r["slug"] for r in raw["routes"]}:
        raw["routes"] = [r for r in raw["routes"] if r["slug"] not in removed]
        notes.append("removed " + ", ".join(sorted(removed)))
    old_components = {c["name"] for c in raw["components"]}
    raw["components"] += [c for c in decision.get("add_components") or [] if isinstance(c, dict)]
    old_routes = {r["slug"] for r in raw["routes"]}
    for route in decision.get("add_routes") or []:
        if isinstance(route, dict):
            raw["routes"].append({**route, "uses": route.get("uses") or []})
    for name, records in (decision.get("data_additions") or {}).items():
        if isinstance(records, list):
            raw["data"].setdefault(name, []).extend(r for r in records if isinstance(r, dict))
    raw["packages"] = list(raw["packages"]) + list(decision.get("packages") or [])
    # normalize_plan re-derives names and files; keep existing component/page identities stable
    new_plan = normalize_plan(raw, len(project["images"]))

    specs = {spec["path"]: spec for spec in generated_files(new_plan)}
    current = {path: files.get(path) for path in specs}
    jobs = []
    for spec in specs.values():
        is_new = (spec["kind"] == "component" and spec["name"] not in old_components) or (
            spec["kind"] == "page" and spec["route"]["slug"] not in old_routes)
        if is_new or not current.get(spec["path"]):
            jobs.append((spec, None, None))
    added = {job[0]["path"] for job in jobs}
    for item in decision.get("edit_files") or []:
        if isinstance(item, dict) and item.get("path") in specs and item["path"] not in added and item.get("instruction"):
            jobs.append((specs[item["path"]], str(item["instruction"]), current[item["path"]]))

    if not jobs and not notes:
        session.log("No changes needed", "done", model)
        return
    sources = {path: source for path, source in current.items() if source}
    sources.update(_generate(session, new_plan, jobs, existing=sources) if jobs else {})
    changed = notes + [f"{'added' if job[1] is None else 'updated'} {job[0]['path']}" for job in jobs]
    version = projects.write_version(session.pid, assemble(new_plan, sources), message[:80], pages=page_slugs(new_plan))
    session.log(f"Version {version} is live: {', '.join(changed)}", "done")
    session.update(name=new_plan["app_name"])


# ---------------------------------------------------------------- preview

def esm_url(spec: str) -> str:
    """The esm.sh address for a library import.

    Every library treats all the other allowed libraries as shared (resolved through the page's
    import map), so the page has exactly one copy of each: two Reacts break every hook, and drei
    with its own react-three-fiber breaks every 3D scene. The list is the same for every project,
    so esm.sh builds each library once and then serves it from cache to everyone.
    """
    package = package_of(spec)
    if package in ("react", "three"):
        external = []
    elif package == "react-dom":
        external = ["react"]
    else:
        external = sorted(p for p in PACKAGES if p != package)
    query = f"?external={','.join(external)}" if external else ""
    return f"https://esm.sh/{package}@{PACKAGES[package]}{spec[len(package):]}{query}"


CORE_SPECS = sorted(PACKAGES) + ["react-dom/client", "react/jsx-runtime"]


def import_map(files: dict[str, str], plan: dict) -> dict:
    """Map every library (and every library subpath the project imports) to its esm.sh URL."""
    specs = set(CORE_SPECS)  # all of them, since any library may import another as shared
    for path, source in files.items():
        if path.startswith("src/") and path.endswith((".js", ".jsx")):
            specs.update(s for s in imports_of(source) if not s.startswith(".") and package_of(s) in PACKAGES)
    imports = {spec: esm_url(spec) for spec in sorted(specs)}
    imports["three/"] = f"https://esm.sh/three@{PACKAGES['three']}/"  # three/examples/... inside libraries
    return {"imports": imports}


def warm_cdn() -> None:
    """Ask esm.sh for every library once (and the modules each entry points to), so the first
    preview after a deploy doesn't wait for esm.sh to build them. Runs in the background."""
    import httpx

    seen = set()
    with httpx.Client(timeout=90, follow_redirects=True) as client:
        for spec in CORE_SPECS:
            try:
                entry = client.get(esm_url(spec)).text
                for path in re.findall(r'["\'](/[^"\']+\.m?js)["\']', entry)[:6]:
                    if path not in seen:
                        seen.add(path)
                        client.get("https://esm.sh" + path)
            except Exception as error:  # warming is best-effort
                logger.info("CDN warm-up skipped %s: %s", spec, error)


def runner_html(files: dict[str, str], plan: dict) -> str:
    """The preview page: bundles the project in the browser with esbuild and runs it."""
    sources = {path: content for path, content in files.items() if path.startswith("src/")}
    payload = json.dumps(sources, ensure_ascii=False).replace("</", "<\\/")
    config = json.dumps({"theme": tailwind_theme(plan["theme"])})
    routes = json.dumps({("" if r["slug"] == "index" else r["slug"]): r["slug"] for r in plan["routes"] if not r["param"]})
    title = plan["app_name"].replace("<", "")
    return RUNNER_TEMPLATE.replace("__TITLE__", title) \
        .replace("__FONTS__", _fonts_href(plan["theme"])) \
        .replace("__TAILWIND__", TAILWIND_PLAY) \
        .replace("__TAILWIND_CONFIG__", config) \
        .replace("__IMPORT_MAP__", json.dumps(import_map(files, plan))) \
        .replace("__ESBUILD__", ESBUILD_VERSION) \
        .replace("__ROUTES__", routes) \
        .replace("__FILES__", payload)


RUNNER_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="__FONTS__">
<script type="importmap">__IMPORT_MAP__</script>
<script src="__TAILWIND__"></script>
<script>tailwind.config = __TAILWIND_CONFIG__;</script>
<style>
  #ns-loading { position: fixed; inset: 0; display: grid; place-items: center; background: #f6f1e6; color: #3d3830; font: 15px/1.5 system-ui, sans-serif; z-index: 99999; transition: opacity .3s; }
  #ns-loading b { display: block; font-size: 17px; margin-bottom: 4px; }
  #ns-error { position: fixed; inset: auto 12px 12px 12px; max-height: 45vh; overflow: auto; background: #2a1512; color: #ffd9d0; border: 1px solid #7a2e22; border-radius: 10px; padding: 14px 16px; font: 12.5px/1.5 ui-monospace, Consolas, monospace; white-space: pre-wrap; z-index: 99998; display: none; }
  #ns-error b { color: #fff; font-family: system-ui, sans-serif; font-size: 14px; display: block; margin-bottom: 6px; }
</style>
</head>
<body>
<div id="root"></div>
<div id="ns-loading"><div style="text-align:center"><b>Preparing the preview…</b>Bundling the project in your browser. The first time can take a few seconds.</div></div>
<div id="ns-error"></div>
<script type="application/json" id="ns-files">__FILES__</script>
<script src="https://cdn.jsdelivr.net/npm/esbuild-wasm@__ESBUILD__/lib/browser.min.js"></script>
<script>
(function () {
  "use strict";
  var FILES = JSON.parse(document.getElementById("ns-files").textContent);
  var ROUTES = __ROUTES__;
  var inFrame = window.parent !== window;
  function post(message) {
    if (inFrame) window.parent.postMessage(Object.assign({ source: "naturalsketch" }, message), "*");
  }
  function currentPage() {
    var path = (location.hash.replace(/^#\/?/, "").split(/[/?]/)[0]) || "";
    return ROUTES[path] || (path ? path : "index");
  }
  var reported = {};
  function showError(title, message) {
    var box = document.getElementById("ns-error");
    box.innerHTML = "";
    var b = document.createElement("b");
    b.textContent = title;
    box.appendChild(b);
    box.appendChild(document.createTextNode(message));
    box.style.display = "block";
    var key = title + message;
    if (!reported[key]) {
      reported[key] = true;
      post({ type: "error", page: currentPage(), message: (title + ": " + message).slice(0, 1500) });
    }
  }
  window.__NS_PREVIEW__ = true; // full-stack projects: use sample data here instead of calling the API
  window.__nsReport = function (error, stack) {
    showError("Runtime error", String((error && (error.stack || error.message)) || error) + (stack ? "\n" + stack.trim().split("\n").slice(0, 6).join("\n") : ""));
  };
  window.addEventListener("error", function (e) {
    if (e.error || e.message) window.__nsReport(e.error || e.message);
  });
  window.addEventListener("unhandledrejection", function (e) {
    window.__nsReport(e.reason || "Unhandled promise rejection");
  });
  window.addEventListener("hashchange", function () { post({ type: "page", page: currentPage() }); });
  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "ns:reset") {
      Object.keys(localStorage).forEach(function (k) { if (k.indexOf("ns-app:") === 0) localStorage.removeItem(k); });
      location.reload();
    }
  });

  var EXTS = ["", ".jsx", ".js", ".tsx", ".ts", "/index.jsx", "/index.js"];
  function resolve(dir, spec) {
    var parts = (dir ? dir.split("/") : []).concat(spec.split("/"));
    var out = [];
    parts.forEach(function (p) { if (p === "..") out.pop(); else if (p && p !== ".") out.push(p); });
    var base = out.join("/");
    for (var i = 0; i < EXTS.length; i++) if (FILES[base + EXTS[i]] != null) return base + EXTS[i];
    return null;
  }
  var vfs = {
    name: "ns-vfs",
    setup: function (build) {
      build.onResolve({ filter: /.*/ }, function (args) {
        if (args.kind === "entry-point") return { path: args.path, namespace: "vfs" };
        if (args.path.charAt(0) === "." || args.path.charAt(0) === "/") {
          var dir = args.importer.split("/").slice(0, -1).join("/");
          var found = resolve(args.path.charAt(0) === "/" ? "" : dir, args.path.replace(/^\//, ""));
          if (!found) return { errors: [{ text: "Cannot find \"" + args.path + "\" (imported by " + args.importer + ")" }] };
          return { path: found, namespace: "vfs" };
        }
        return { path: args.path, external: true };
      });
      build.onLoad({ filter: /.*/, namespace: "vfs" }, function (args) {
        var text = FILES[args.path];
        if (/\.css$/.test(args.path)) {
          var css = text.replace(/@tailwind[^;]*;/g, "");
          return { loader: "js", contents: "const s=document.createElement('style');s.type='text/tailwindcss';s.textContent=" + JSON.stringify(css) + ";document.head.appendChild(s);" };
        }
        if (/\.json$/.test(args.path)) return { loader: "json", contents: text };
        return { loader: /\.tsx?$/.test(args.path) ? "tsx" : "jsx", contents: text };
      });
    },
  };

  function done() {
    var l = document.getElementById("ns-loading");
    l.style.opacity = "0";
    setTimeout(function () { l.remove(); }, 300);
  }
  esbuild.initialize({ wasmURL: "https://cdn.jsdelivr.net/npm/esbuild-wasm@__ESBUILD__/esbuild.wasm" })
    .then(function () {
      return esbuild.build({
        entryPoints: ["src/main.jsx"], bundle: true, write: false, format: "esm", jsx: "automatic",
        target: "es2020", plugins: [vfs], logLevel: "silent", sourcemap: "inline",
      });
    })
    .then(function (result) {
      var url = URL.createObjectURL(new Blob([result.outputFiles[0].text], { type: "text/javascript" }));
      // Libraries come from esm.sh; a rarely used one may need building there the first time.
      var slow = setTimeout(function () {
        var l = document.getElementById("ns-loading");
        if (l) l.querySelector("div").innerHTML = "<b>Still loading the libraries…</b>3D and animation libraries can take up to a minute the very first time.";
      }, 12000);
      return import(url).finally(function () { clearTimeout(slow); });
    })
    .then(function () {
      done();
      post({ type: "page", page: currentPage() });
    })
    .catch(function (error) {
      done();
      var errors = (error && error.errors) || [];
      if (errors.length) {
        showError("Build error", errors.slice(0, 5).map(function (e) {
          var at = e.location ? e.location.file + ":" + e.location.line + ":" + e.location.column + "\n  " + e.location.lineText + "\n" : "";
          return at + e.text;
        }).join("\n\n"));
      } else {
        showError("Couldn't start the preview", String((error && error.message) || error));
      }
    });
})();
</script>
</body>
</html>
"""
