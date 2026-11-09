# NaturalSketch

An AI app builder in the spirit of Lovable: upload sketches, wireframes or screenshots, list the
features you want, and get a **working multi-page prototype**. Then keep refining it by chat.

Runs entirely on **free tiers** of Gemini, Groq and OpenRouter, with automatic fallback between them.

## Two kinds of project

- **Website prototype:** a multi-page HTML app on a small shared runtime (navigation, a persistent mock
  database, cart and wishlist state, mock login, toasts). The fastest to build.
- **React app:** a real Vite + React 18 + Tailwind CSS project with React Router, and when it suits the idea
  three.js / React Three Fiber 3D scenes, Framer Motion and GSAP animation, and smooth scrolling. The AI plans
  routes and components, writes the layout and components first and then the pages that use them, and every file
  is checked (imports, conventions) before it is accepted. The preview bundles the project in the browser with
  esbuild-wasm and loads libraries from esm.sh, and errors it reports are sent back to the AI to fix. The ZIP export
  is a normal project: `npm install && npm run dev`.

## How it works

1. **Analyse.** A vision model reads your images: layout, components, text and style. Paper sketches
   are also cleaned up with OpenCV (shadow removal, adaptive thresholding) so the lines are clear.
2. **Plan.** The app gets a name, pages, navigation, a theme and realistic sample data, and every
   listed feature is mapped to a page.
3. **Build.** Pages are generated in parallel. Each page runs on a shared runtime
   (`runtime/ns-runtime.js`) that provides the navigation shell, a persistent mock database, app
   state (cart, wishlist...), mock login, toasts and dialogs, so buttons and forms actually work.
4. **Iterate.** Chat to change things. The assistant decides which pages to add, edit or remove,
   and theme or name changes apply instantly. Every change is a new version you can restore.
5. **Export.** Download a ZIP of plain HTML that runs anywhere (Netlify Drop, GitHub Pages...).

## Model fallback

Each step has an ordered chain of models across the three providers (see `llm.py`). If a model is
rate limited, overloaded, times out or returns unusable output, the next one takes over, and the
failed model sits out for a short cooldown so parallel page builds skip it.

| Step | Chain |
| --- | --- |
| Image analysis | Gemini 3.5 Flash → Gemini Flash (latest) → OpenRouter free → Gemini Flash-Lite |
| Planning | Groq gpt-oss-120b → Gemini 3.5 Flash → OpenRouter free → Groq Qwen → Gemini Flash-Lite |
| Page code | Gemini 3.5 Flash → OpenRouter free → Gemini Flash (latest) → Flash-Lite → Groq |
| Chat edits | Groq gpt-oss-120b → Groq Qwen → Gemini 3.5 Flash → OpenRouter free → Flash-Lite |

`openrouter/free` is OpenRouter's router that picks whichever free model is available. The build
log shows which model actually handled each step. Override any chain in `.env`, e.g.
`NS_CHAIN_CODE=gemini:gemini-3.5-flash,openrouter:openrouter/free`.

## Setup

1. Copy `.env.example` to `.env` and add your keys (any one is enough; all three gives fallback):
   [Gemini](https://aistudio.google.com/apikey), [Groq](https://console.groq.com/keys),
   [OpenRouter](https://openrouter.ai/settings/keys).
   Add your Supabase database: in the project click **Connect → Session pooler**, put the URI
   (without the password) in `DATABASE_URL` and the password in `DATABASE_PASSWORD`.
   The tables are created automatically on first start.
2. Install and run:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python server.py
```

3. Open http://localhost:8000

All data lives in Supabase, in a `naturalsketch` schema that Supabase's public Data API doesn't
expose, so it's only reachable through this server. Nothing is stored on local disk, so the server
can be deployed anywhere.

Generated apps are served from `http://127.0.0.1:8000`, a separate origin, so code written by the
AI can't access the builder or its API. The server only listens on this computer.

## Files

| File | Role |
| --- | --- |
| `server.py` | FastAPI server: API, preview hosting, ZIP export |
| `builder.py` | Pipeline and prompts: analyse → plan → build pages, chat edits, versions |
| `react_builder.py` | React projects: plan, file generation and checks, project scaffold, in-browser preview |
| `auth.py` | Accounts via Supabase Auth (email and Google sign-in) |
| `llm.py` | Gemini / Groq / OpenRouter client with fallback chains and cooldowns |
| `projects.py` | Storage in Supabase Postgres (schema `naturalsketch`): projects, uploads, versions, files, chat, logs |
| `migrate_local_data.py` | One-time import of projects from the old `data/` folder |
| `vision_processor.py` | Sketch detection and OpenCV clean-up |
| `runtime/ns-runtime.js` | Shared runtime included in every generated app |
| `web/` | The builder UI |

`samples/plant_shop_sketch.jpg` is a test sketch to try.
