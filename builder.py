"""The build pipeline: analyse images -> plan the app -> generate pages -> assemble the site.

Also handles chat edits (decide what to change -> regenerate only those pages)
and runs both as background jobs so the UI can poll progress.
"""

import html
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import llm
import projects
import vision_processor

logger = logging.getLogger("naturalsketch.builder")

RUNTIME_JS = Path(__file__).parent / "runtime" / "ns-runtime.js"
TAILWIND_CDN = "https://cdn.tailwindcss.com"
LUCIDE_CDN = "https://unpkg.com/lucide@0.460.0/dist/umd/lucide.min.js"
CHART_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
MAX_PAGES = 12
PARALLEL_PAGES = 3

COLOR_KEYS = ["primary", "secondary", "accent", "background", "surface", "foreground", "muted", "border"]
DEFAULT_THEME = {
    "primary": "#6366f1", "secondary": "#0ea5e9", "accent": "#f59e0b", "background": "#f8fafc",
    "surface": "#ffffff", "foreground": "#0f172a", "muted": "#64748b", "border": "#e2e8f0",
    "font_heading": "Inter", "font_body": "Inter", "radius": "large", "mode": "light",
}
RADIUS_GUIDE = {
    "none": "square corners (rounded-none) everywhere",
    "small": "rounded for buttons/inputs, rounded-md for cards",
    "medium": "rounded-lg for buttons/inputs, rounded-xl for cards",
    "large": "rounded-xl for buttons/inputs, rounded-2xl for cards",
    "full": "rounded-full pills for buttons/inputs/badges, rounded-3xl for cards",
}

# ---------------------------------------------------------------- prompts

ANALYSE_SYSTEM = """You are a senior product designer. You receive images of an app's UI (hand-drawn sketches, wireframes or screenshots) plus the user's description and feature list. Describe precisely what you see so a developer could rebuild it without the images.

Reading sketches: boxes with an X are images, squiggly or straight lines are text, small labelled boxes are buttons, long empty boxes are inputs, rows of labels at the top are navigation.

Return ONLY a JSON object:
{
  "app_type": "e.g. online store, SaaS dashboard, booking site",
  "summary": "one paragraph: what the product is and who it is for",
  "screens": [
    {"image": 0, "name": "what this screen is", "layout": "structure from top to bottom: columns, sidebar, grids, proportions",
     "components": ["every component you can see, with details"], "text_seen": ["headings, labels and copy you can read"]}
  ],
  "style": {"source": "sketch|screenshot|mixed", "vibe": "e.g. minimal, playful, corporate, luxurious",
            "colors": ["#hex colours visible in screenshots; empty for black-and-white sketches"],
            "mode": "light|dark", "notes": "typography, density, corner rounding, imagery, shadows"}
}"""

PLAN_SYSTEM = """You are a product architect. Turn a product idea (the user's description, required features and, when available, an analysis of their UI sketches or screenshots) into a plan for a multi-page, clickable web prototype. Return ONLY a JSON object:

{
  "app_name": "short brand name",
  "tagline": "one line",
  "logo_icon": "lucide icon name",
  "layout": "topnav or sidebar (sidebar for dashboards, admin panels and SaaS tools; topnav for websites and stores)",
  "currency": "USD",
  "theme": {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "background": "#hex", "surface": "#hex",
            "foreground": "#hex", "muted": "#hex", "border": "#hex", "font_heading": "Google Font name",
            "font_body": "Google Font name", "radius": "none|small|medium|large|full", "mode": "light|dark"},
  "auth": {"enabled": false, "login_page": "login"},
  "pages": [
    {"slug": "index", "title": "Home", "nav": true, "icon": "home", "shell": true, "auth_required": false,
     "image": null, "badge": null, "purpose": "what the page is for",
     "sections": ["ordered sections/components, with specifics"], "features": ["features from the list this page implements"]}
  ],
  "data": {"collection_name": [{"id": "1", "field": "value"}]},
  "state": [{"key": "cart", "shape": "array of {productId, qty}", "used_by": ["products", "cart"]}]
}

Rules:
- 3 to 8 pages; the first page has slug "index". Slugs are lowercase-with-hyphens. Icons are Lucide names.
- Every feature from the list is implemented by at least one page. Add the pages a real product of this kind needs.
- Detail pages (product, article, invoice...) read ?id= from the URL and have "nav": false.
- If features involve accounts, profiles, checkout or private data: enable auth with a "login" page ("shell": false, "nav": false) and set auth_required on private pages.
- "image": 0-based index of the uploaded image this page is based on, or null.
- "badge": a live count shown in the navigation: a state key holding an array (e.g. "cart") or "db:<collection>". Usually null.
- data: 2 to 5 collections with 5 to 10 realistic, specific records each; string ids; every field the pages need (prices, ISO dates, statuses, ratings, image URLs like https://picsum.photos/seed/<word>/600/400).
- theme: follow the colours and mood of screenshots; for sketches or no images pick a distinctive palette that suits the product. Keep text readable. Real Google Fonts only."""

RUNTIME_DOC = """Runtime API (global `NS`, already loaded on every page):
- NS.db('<collection>') -> .all(), .get(id), .find(fn), .filter(fn), .count(fn?), .add(obj) (returns the saved record with a new id), .update(id, patch), .remove(id). Seeded with the app data; changes persist across pages.
- NS.store.get(key, fallback), NS.store.set(key, value), NS.store.update(key, fn, fallback), NS.store.remove(key): persistent app state (cart, favourites, settings, drafts...).
- NS.on(key, fn): calls fn(newValue) whenever a store key or 'db:<collection>' changes. Returns an unsubscribe function.
- NS.auth.user() -> {name, email, ...} or null; NS.auth.login({name, email}); NS.auth.logout(). On a login page: after NS.auth.login(...), NS.go(NS.params().next || 'index').
- NS.go('slug', {id: '3'}) navigates; NS.href('slug', params) builds a link; NS.params() returns the query parameters.
- NS.toast('Saved', 'success' | 'error' | 'info').
- NS.modal({title, html, confirmText, cancelText, onConfirm(bodyElement)}): dialog; return false from onConfirm to keep it open.
- NS.money(amount), NS.date(isoString), NS.esc(text) (HTML-escape)."""

PAGE_SYSTEM = f"""You are a senior frontend engineer and product designer. You build one page of a multi-page web app prototype. It must look like a polished, production-quality product AND work: every button, form, filter, tab and link does something real through the runtime below.

Output format:
- Return ONLY the HTML that goes inside the page container: the page's markup followed by at most one <script> with the page logic. No <html>, <head> or <body>, no markdown fences, no explanations.
- NEVER output a site header, top navigation bar, logo bar, sidebar or site footer, even if the sketch shows one: the shell already renders the app's logo, page links, cart/count badges, sign-in menu and footer around your content. Start directly with the page's first section.
- Style with Tailwind CSS (already loaded). Use the theme colour classes: bg-primary with text-primary-foreground, bg-secondary, bg-accent, bg-background, bg-surface (cards, panels), text-foreground, text-muted (secondary text), border-border, plus opacity modifiers like bg-primary/10. Never hard-code hex colours. Headings use font-heading.
- Icons: <i data-lucide="icon-name" class="w-4 h-4"></i> with Lucide names (search, shopping-cart, trash-2, plus, star, heart, user, settings, chevron-right...). They render automatically, also in HTML you insert later.
- Images: use image URLs from the data, otherwise https://picsum.photos/seed/<descriptive-word>/<width>/<height>.
- Charts only if the page needs them: Chart.js is loaded as `Chart` when your script contains `new Chart(`.
- Link to other pages with href="<slug>.html" (detail pages: <slug>.html?id=<id>). Only link to pages that exist.

{RUNTIME_DOC}

Making it work:
- Render lists, tables, cards, stats and counts from NS.db(...) in your script; never hard-code records that exist in the data. Re-render after changes (NS.on('db:<collection>', render) or call render() again).
- Forms validate input, save through NS.db / NS.store / NS.auth, confirm with NS.toast, then reset or navigate.
- Search boxes, filters, sorting and tabs update the rendered data live.
- Show empty states for empty lists and a friendly not-found state on detail pages with a missing id.
- Escape user-entered text with NS.esc() when building HTML strings.
- Wrap the script in an IIFE; modern vanilla JS only (no frameworks, no imports, no localStorage: use NS.store).

Design:
- When a reference sketch or screenshot is attached or described, follow its layout and components closely.
- Strong visual hierarchy, generous spacing, consistent corners, subtle borders and shadows, hover and focus states, fully responsive from 360px wide to desktop.
- Realistic, specific copy for this product. No lorem ipsum, no "Feature 1"."""

EDIT_SYSTEM = PAGE_SYSTEM + """

You are now editing an existing page. Apply the requested change, keep everything else that already works, and return the complete updated page content in the same format."""

ROUTER_SYSTEM = """You are the assistant inside an AI app builder. The user is iterating on a generated multi-page prototype. Decide what to change and return ONLY a JSON object:
{
  "reply": "1-3 friendly sentences telling the user what you are changing, or answering their question",
  "app_name": null,
  "theme": null,
  "layout": null,
  "add_pages": [],
  "edit_pages": [],
  "remove_pages": [],
  "data_additions": {},
  "state_additions": [],
  "badges": {}
}
- app_name: a new name, or null.
- theme: only the theme fields to change (primary, secondary, accent, background, surface, foreground, muted, border, font_heading, font_body, radius, mode), or null.
- layout: "topnav" or "sidebar", or null.
- add_pages: [{"slug", "title", "nav", "icon", "shell", "auth_required", "badge", "purpose", "sections", "features"}].
- edit_pages: [{"slug": "an existing slug", "instruction": "precise description of the change for the page developer"}].
- remove_pages: slugs to delete (never "index").
- data_additions: {"collection": [new records with string ids]}.
- state_additions: new shared NS.store keys, [{"key": "wishlist", "shape": "array of productId strings", "used_by": ["index", "wishlist"]}]. Name the same key in every page instruction that uses it.
- badges: live counts in the navigation, {"page-slug": "store key holding an array" or "db:<collection>"}, e.g. {"wishlist": "wishlist"}. Works for existing and new pages.

Rules:
- Theme, colour, font, name and layout changes apply to every page automatically: do not also edit pages for them.
- When switching between light and dark, or changing the background, send a complete palette (background, surface, foreground, muted, border, primary) that reads well together.
- Only edit pages that need to change. For app-wide content requests edit every affected page.
- When adding a page, also edit the existing pages that should link to it (navigation links are automatic when "nav" is true).
- If the user reports an error on a page, edit that page with an instruction to fix it.
- If the user only asks a question, answer in "reply" and change nothing."""


# ---------------------------------------------------------------- plan normalisation

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def _hex(value, default: str) -> str:
    value = str(value or "").strip()
    return value if HEX.match(value) else default


def _slug(value) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:40] or "page"


def _icon(value, default: str = "circle") -> str:
    return re.sub(r"[^a-z0-9-]", "", str(value or "").lower())[:40] or default


def _font(value, default: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ]", "", str(value or "")).strip()[:40] or default


def _text_list(value, limit: int = 20) -> list[str]:
    return [str(item)[:300] for item in value[:limit]] if isinstance(value, list) else []


def _luminance(color: str) -> float:
    digits = color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    channels = [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def readable_on(color: str) -> str:
    """White or near-black, whichever contrasts more with the colour."""
    lum = _luminance(color)
    return "#ffffff" if 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.06 else "#0f172a"


def _contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _mix(a: str, b: str, amount: float) -> str:
    """Blend colour a towards colour b by `amount` (0..1)."""
    def rgb(color):
        digits = color.lstrip("#")
        digits = "".join(c * 2 for c in digits) if len(digits) == 3 else digits
        return [int(digits[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(rgb(a), rgb(b)))


def _readable_theme(theme: dict) -> None:
    """Models often change one colour (e.g. only the background); repair the rest so text stays readable."""
    dark = _luminance(theme["background"]) < 0.2
    if _contrast(theme["foreground"], theme["background"]) < 4.5:
        theme["foreground"] = "#f1f5f9" if dark else "#0f172a"
    if _contrast(theme["foreground"], theme["surface"]) < 4.5:
        theme["surface"] = _mix(theme["background"], "#ffffff", 0.07) if dark else "#ffffff"
    if _contrast(theme["muted"], theme["surface"]) < 3:
        theme["muted"] = _mix(theme["foreground"], theme["surface"], 0.35)
    if not 1.25 <= _contrast(theme["border"], theme["surface"]) <= 3:  # visible but subtle
        theme["border"] = _mix(theme["surface"], theme["foreground"], 0.18)


def normalize_plan(raw: dict, image_count: int) -> dict:
    """Validate and repair a plan from any model so the rest of the pipeline can trust it."""
    theme_in = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {key: _hex(theme_in.get(key), DEFAULT_THEME[key]) for key in COLOR_KEYS}
    _readable_theme(theme)
    theme["font_heading"] = _font(theme_in.get("font_heading"), DEFAULT_THEME["font_heading"])
    theme["font_body"] = _font(theme_in.get("font_body"), DEFAULT_THEME["font_body"])
    theme["radius"] = theme_in.get("radius") if theme_in.get("radius") in RADIUS_GUIDE else "large"
    theme["mode"] = "dark" if _luminance(theme["background"]) < 0.2 else "light"
    theme["primary_foreground"] = readable_on(theme["primary"])

    auth_in = raw.get("auth") if isinstance(raw.get("auth"), dict) else {}
    auth = {"enabled": bool(auth_in.get("enabled")), "login_page": _slug(auth_in.get("login_page") or "login")}

    pages, seen = [], set()
    for item in raw.get("pages") or []:
        if not isinstance(item, dict):
            continue
        slug = _slug(item.get("slug") or item.get("title"))
        if slug in seen:
            continue
        seen.add(slug)
        image = item.get("image")
        badge = re.sub(r"[^a-z0-9_:-]", "", str(item.get("badge") or "").lower()) or None
        pages.append({
            "slug": slug,
            "title": str(item.get("title") or slug.replace("-", " ").title())[:40],
            "nav": item.get("nav") is not False,
            "icon": _icon(item.get("icon")),
            "shell": item.get("shell") is not False,
            "auth_required": bool(item.get("auth_required")) and auth["enabled"],
            "image": image if isinstance(image, int) and 0 <= image < image_count else None,
            "badge": badge,
            "purpose": str(item.get("purpose") or "")[:600],
            "sections": _text_list(item.get("sections")),
            "features": _text_list(item.get("features")),
        })
    if not pages:
        raise ValueError("the plan has no pages")
    if "index" not in seen:
        pages[0]["slug"] = "index"
    pages.sort(key=lambda page: page["slug"] != "index")
    if auth["enabled"] and auth["login_page"] not in {page["slug"] for page in pages}:
        pages.append({
            "slug": auth["login_page"], "title": "Sign in", "nav": False, "icon": "log-in", "shell": False,
            "auth_required": False, "image": None, "badge": None,
            "purpose": "Sign in / create account screen (mock auth through NS.auth.login).",
            "sections": ["Brand panel", "Sign-in form with email and password", "Toggle to a sign-up form"], "features": [],
        })

    data = {}
    raw_data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    for name, records in list(raw_data.items())[:10]:
        key = re.sub(r"[^a-z0-9_]", "_", str(name).lower()).strip("_")
        if not key or not isinstance(records, list):
            continue
        clean = []
        for index, record in enumerate(r for r in records[:60] if isinstance(r, dict)):
            record = dict(record)
            record["id"] = str(record.get("id") or index + 1)
            clean.append(record)
        data[key] = clean

    return {
        "app_name": str(raw.get("app_name") or "My App").strip()[:40],
        "tagline": str(raw.get("tagline") or "")[:140],
        "logo_icon": _icon(raw.get("logo_icon"), "sparkles"),
        "layout": raw.get("layout") if raw.get("layout") in ("topnav", "sidebar") else "topnav",
        "currency": re.sub(r"[^A-Z]", "", str(raw.get("currency") or "USD").upper())[:3] or "USD",
        "theme": theme,
        "auth": auth,
        "pages": pages[:MAX_PAGES],
        "data": data,
        "state": [s for s in raw.get("state") or [] if isinstance(s, dict)][:10],
    }


# ---------------------------------------------------------------- page output

HEADER_BLOCK = re.compile(r"<header\b.*?</header>", re.DOTALL | re.IGNORECASE)
FOOTER_BLOCK = re.compile(r"<footer\b.*?</footer>", re.DOTALL | re.IGNORECASE)


def _drop_first(pattern: re.Pattern, text: str, markers: tuple[str, ...]) -> str:
    """Remove the first block matching `pattern` that contains one of the markers."""
    for match in pattern.finditer(text):
        if any(marker in match.group(0).lower() for marker in markers):
            return text[:match.start()] + text[match.end():]
    return text


def clean_body(text: str, shell: bool = True) -> str:
    """Turn a model reply into page content, or raise so the next model is tried."""
    fenced = re.findall(r"```(?:html)?\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = max(fenced, key=len)
    body = re.search(r"<body[^>]*>(.*)</body>", text, flags=re.DOTALL | re.IGNORECASE)
    if body:
        text = body.group(1)
    text = re.sub(r"<!DOCTYPE[^>]*>|</?html[^>]*>|<head>.*?</head>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Assets the wrapper already loads.
    text = re.sub(
        r"<script[^>]+src=[\"'][^\"']*(tailwindcss|lucide|chart\.js|chart\.umd|app-config|ns-runtime)[^\"']*[\"'][^>]*>\s*</script>",
        "", text, flags=re.IGNORECASE,
    )
    text = text.replace('id="ns-page"', "").strip()
    if shell:  # the shell draws the site header and footer; drop duplicates a model added anyway
        text = _drop_first(HEADER_BLOCK, text, ("<nav", "sticky", "fixed top-0"))
        text = _drop_first(FOOTER_BLOCK, text, ("©", "&copy;", "rights reserved")).strip()
    if len(text) < 300 or "<" not in text:
        raise ValueError("the page is empty")
    if len(re.findall(r"<script\b", text, re.IGNORECASE)) != len(re.findall(r"</script>", text, re.IGNORECASE)):
        raise ValueError("the page code is incomplete")
    return text


def placeholder_body(page: dict) -> str:
    title = html.escape(page["title"])
    return f"""<section class="max-w-2xl mx-auto px-4 py-24 text-center">
  <div class="w-14 h-14 mx-auto rounded-2xl bg-primary/10 text-primary grid place-items-center"><i data-lucide="hourglass" class="w-6 h-6"></i></div>
  <h1 class="mt-6 text-3xl font-bold font-heading">{title}</h1>
  <p class="mt-3 text-muted">This page could not be generated because every AI provider was busy. Ask in the chat: "Regenerate the {title} page".</p>
</section>"""


def tailwind_config(theme: dict) -> str:
    stack = ["ui-sans-serif", "system-ui", "sans-serif"]
    colors = {key: theme[key] for key in COLOR_KEYS}
    colors["primary"] = {"DEFAULT": theme["primary"], "foreground": theme["primary_foreground"]}
    return json.dumps({"theme": {"extend": {
        "colors": colors,
        "fontFamily": {"heading": [theme["font_heading"], *stack], "body": [theme["font_body"], *stack]},
    }}})


def render_page(plan: dict, page: dict, body: str) -> str:
    theme = plan["theme"]
    fonts = "".join(
        f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family={font.replace(" ", "+")}:wght@400;500;600;700;800&display=swap">\n'
        for font in dict.fromkeys([theme["font_heading"], theme["font_body"]])
    )
    chart = f'<script src="{CHART_CDN}"></script>\n' if "new Chart" in body else ""
    title = html.escape(f'{page["title"]} · {plan["app_name"]}')
    return f"""<!DOCTYPE html>
<html lang="en" data-page="{page['slug']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
{fonts}<script src="{TAILWIND_CDN}"></script>
<script>tailwind.config = {tailwind_config(theme)};</script>
<style>h1,h2,h3,h4,h5,h6{{font-family:'{theme["font_heading"]}',ui-sans-serif,system-ui,sans-serif}}</style>
<script src="{LUCIDE_CDN}"></script>
{chart}<script src="assets/app-config.js"></script>
<script src="assets/ns-runtime.js"></script>
</head>
<body class="bg-background text-foreground font-body antialiased min-h-screen">
<div id="ns-page">
{body}
</div>
</body>
</html>
"""


def render_site(plan: dict, bodies: dict[str, str], pid: str) -> dict[str, str]:
    config = {
        "id": pid,
        "app_name": plan["app_name"],
        "tagline": plan["tagline"],
        "logo_icon": plan["logo_icon"],
        "layout": plan["layout"],
        "currency": plan["currency"],
        "auth": plan["auth"],
        "pages": [
            {k: page[k] for k in ("slug", "title", "nav", "icon", "badge", "auth_required", "shell")}
            for page in plan["pages"]
        ],
    }
    files = {
        "assets/ns-runtime.js": RUNTIME_JS.read_text(encoding="utf-8"),
        "assets/app-config.js": f"window.NS_CONFIG = {json.dumps(config, ensure_ascii=False)};\n"
                                f"window.NS_DATA = {json.dumps(plan['data'], ensure_ascii=False)};\n",
        "_src/plan.json": json.dumps(plan, ensure_ascii=False, indent=1),
    }
    for page in plan["pages"]:
        body = bodies.get(page["slug"]) or placeholder_body(page)
        files[f"{page['slug']}.html"] = render_page(plan, page, body)
        files[f"_src/pages/{page['slug']}.html"] = body
    return files


# ---------------------------------------------------------------- prompt builders

def _features_text(project: dict) -> str:
    return "\n".join(f"- {feature}" for feature in project["features"]) or "(none listed)"


def _images_for(project: dict, indexes: list[int]) -> tuple[list[tuple[bytes, str]], list[str]]:
    """Attachments for the given uploads (photo, plus a cleaned version of sketches) and their labels."""
    attachments, labels = [], []
    for index in indexes:
        info = project["images"][index]
        data, _mime = projects.read_upload(project["id"], index)
        prepared = vision_processor.prepare(data, info["kind"])
        attachments.append((prepared["original"], "image/jpeg"))
        labels.append(f"Image {index}: {'photo of a hand-drawn sketch' if info['kind'] == 'sketch' else 'screenshot / mockup'}")
        if prepared["cleaned"]:
            attachments.append((prepared["cleaned"], "image/png"))
            labels.append(f"Image {index}: cleaned black-and-white version of the same sketch")
    return attachments, labels


def _data_summary(plan: dict) -> str:
    lines = []
    for name, records in plan["data"].items():
        fields = sorted({key for record in records for key in record})
        sample = json.dumps(records[:2], ensure_ascii=False)[:900]
        lines.append(f"- {name} ({len(records)} records; fields: {', '.join(fields)}). Sample: {sample}")
    return "\n".join(lines) or "(no collections)"


def page_prompt(project: dict, plan: dict, page: dict) -> str:
    theme = plan["theme"]
    pages = "\n".join(
        f"- {p['slug']}.html: {p['title']}: {p['purpose']}{' (needs ?id=)' if not p['nav'] and p['shell'] else ''}"
        for p in plan["pages"]
    )
    if not page["shell"]:
        shell = "This page has NO shell: it is a full-screen page (e.g. sign-in). Design the entire screen, including a link back to index.html."
    elif plan["layout"] == "sidebar":
        shell = "The shell provides a left sidebar with navigation and the user menu. Wrap content in a <div class=\"p-4 sm:p-6 lg:p-8 max-w-7xl\">."
    else:
        shell = "The shell provides a top navigation bar and a footer. Use full-width sections with inner containers <div class=\"max-w-7xl mx-auto px-4\">."

    screen = ""
    brief = project.get("brief") or {}
    if page["image"] is not None:
        notes = [s for s in brief.get("screens") or [] if isinstance(s, dict) and s.get("image") == page["image"]]
        screen = (
            f"\nREFERENCE: the attached image(s) show this page. Follow its layout and components.\n"
            f"Design analysis of the reference: {json.dumps(notes, ensure_ascii=False)[:2500]}\n"
        )
    state = json.dumps(plan["state"], ensure_ascii=False)[:1500] if plan["state"] else "(none)"

    return f"""APP: {plan['app_name']}: {plan['tagline']}
USER'S DESCRIPTION: {project['prompt'] or '(see images)'}
ALL REQUIRED FEATURES:
{_features_text(project)}
STYLE: {json.dumps(brief.get('style') or {}, ensure_ascii=False)[:800]}
THEME: {theme['mode']} mode, heading font {theme['font_heading']}, body font {theme['font_body']}. Corners: {RADIUS_GUIDE[theme['radius']]}.
AUTH: {'enabled, login page is ' + plan['auth']['login_page'] + '.html' if plan['auth']['enabled'] else 'disabled'}

PAGES IN THE APP:
{pages}

DATA COLLECTIONS (all records available through NS.db):
{_data_summary(plan)}

SHARED STATE KEYS: {state}

LAYOUT: {shell}
{screen}
BUILD THIS PAGE: {page['slug']}.html: "{page['title']}"
Purpose: {page['purpose']}
Sections, in order: {json.dumps(page['sections'], ensure_ascii=False)}
Features this page implements: {json.dumps(page['features'], ensure_ascii=False)}"""


def router_prompt(project: dict, plan: dict, message: str) -> str:
    pages = "\n".join(
        f"- {p['slug']} \"{p['title']}\" (nav: {p['nav']}, icon: {p['icon']}): {p['purpose']}" for p in plan["pages"]
    )
    collections = ", ".join(
        f"{name} (fields: {', '.join(sorted({k for r in records for k in r}))})" for name, records in plan["data"].items()
    )
    history = "\n".join(f"{m['role']}: {m['content'][:400]}" for m in project["chat"][-8:])
    return f"""APP: {plan['app_name']}: {plan['tagline']}. Layout: {plan['layout']}. Auth: {plan['auth']['enabled']}.
THEME: {json.dumps(plan['theme'])}
PAGES:
{pages}
DATA COLLECTIONS: {collections or '(none)'}
FEATURES REQUESTED AT THE START:
{_features_text(project)}

RECENT CONVERSATION:
{history or '(none)'}

USER MESSAGE: {message}"""


# ---------------------------------------------------------------- jobs

_running: set[str] = set()
_running_lock = threading.Lock()


def is_running(pid: str) -> bool:
    with _running_lock:
        return pid in _running


def _start(pid: str, job, *args) -> bool:
    with _running_lock:
        if pid in _running:
            return False
        _running.add(pid)

    def run():
        try:
            job(pid, *args)
        except Exception as error:  # last-resort guard so a project never stays "building"
            logger.exception("Job failed for %s", pid)
            try:
                built = projects.load(pid)["current"] is not None
                projects.update(pid, status="ready" if built else "error", error=None if built else str(error))
                projects.add_log(pid, "error", f"Something went wrong: {error}")
            except Exception:
                logger.exception("Could not record the failure for %s", pid)
        finally:
            with _running_lock:
                _running.discard(pid)

    threading.Thread(target=run, daemon=True, name=f"job-{pid}").start()
    return True


def start_build(pid: str) -> bool:
    return _start(pid, _build)


def start_chat(pid: str, message: str) -> bool:
    return _start(pid, _chat, message)


def start_restore(pid: str, version: int) -> bool:
    return _start(pid, _restore, version)


class _Session:
    """A project being worked on by a job; progress goes straight to the database."""

    def __init__(self, pid: str):
        self.pid = pid
        self.project = projects.load(pid)

    def update(self, **fields):
        projects.update(self.pid, **fields)
        self.project.update(fields)

    def log(self, msg: str, kind: str = "info", model: str | None = None):
        projects.add_log(self.pid, kind, msg, model)

    def say(self, content: str):
        projects.add_message(self.pid, "assistant", content)
        self.project["chat"].append({"role": "assistant", "content": content})

    def fallback(self, step: str):
        return lambda label, reason: self.log(f"{step}: {label} {reason}, trying the next model", "warn")


def _generate_pages(session: _Session, plan: dict, jobs: list[tuple[dict, str | None, str | None]]) -> dict[str, str]:
    """jobs: (page, change instruction or None, current body or None). Runs in parallel."""
    project = session.project

    def one(page, instruction, current):
        verb = "Updating" if current else "Building"
        session.log(f"{verb} {page['title']}", "step")
        prompt = page_prompt(project, plan, page)
        images = []
        if current:
            prompt += f"\n\nCURRENT PAGE CONTENT:\n{current}\n\nCHANGE REQUEST: {instruction}"
        elif page["image"] is not None:
            images, labels = _images_for(project, [page["image"]])
            prompt += "\n\nATTACHED: " + "; ".join(labels)
        try:
            body, model = llm.complete(
                "code", EDIT_SYSTEM if current else PAGE_SYSTEM, prompt, images=images, max_tokens=16000,
                validate=lambda text: clean_body(text, page["shell"]), on_fallback=session.fallback(page["title"]),
            )
            session.log(f"{page['title']} ready", "done", model)
            return body
        except llm.LLMError:
            session.log(f"{page['title']}: every provider failed, kept a placeholder", "error")
            return current or placeholder_body(page)

    results = {}
    with ThreadPoolExecutor(max_workers=PARALLEL_PAGES) as pool:
        futures = {pool.submit(one, *job): job[0]["slug"] for job in jobs}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _build(pid: str):
    session = _Session(pid)
    project = session.project
    session.update(status="building", error=None)
    try:
        images = list(range(len(project["images"])))
        if images:
            session.log(f"Analysing {len(images)} image{'s' if len(images) > 1 else ''}", "step")
            attachments, labels = _images_for(project, images)
            prompt = (
                f"USER'S DESCRIPTION: {project['prompt'] or '(none)'}\nREQUIRED FEATURES:\n{_features_text(project)}\n\n"
                "ATTACHED, IN ORDER:\n" + "\n".join(f"{i + 1}. {label}" for i, label in enumerate(labels))
            )
            brief, model = llm.complete(
                "vision", ANALYSE_SYSTEM, prompt, images=attachments, json_mode=True, max_tokens=8000,
                validate=llm.parse_json, on_fallback=session.fallback("Image analysis"),
            )
            session.update(brief=brief)
            session.log(f"Design understood: {str(brief.get('summary', ''))[:220]}", "done", model)

        if project.get("kind") == "react":
            import react_builder  # imported here: it builds on this module

            brief = json.dumps(project["brief"], ensure_ascii=False) if project["brief"] else "(no images)"
            react_builder.build(session, brief, len(images))
            session.update(status="ready")
            return

        session.log("Planning pages, navigation, theme and data", "step")
        prompt = (
            f"USER'S DESCRIPTION: {project['prompt'] or '(see the image analysis)'}\n"
            f"REQUIRED FEATURES:\n{_features_text(project)}\n"
            f"UPLOADED IMAGES: {len(images)}\n"
            f"IMAGE ANALYSIS: {json.dumps(project['brief'], ensure_ascii=False) if project['brief'] else '(no images)'}"
        )
        plan, model = llm.complete(
            "plan", PLAN_SYSTEM, prompt, json_mode=True, max_tokens=12000,
            validate=lambda text: normalize_plan(llm.parse_json(text), len(images)),
            on_fallback=session.fallback("Planning"),
        )
        session.update(name=plan["app_name"])
        session.log(f"Planned {plan['app_name']}: {', '.join(p['title'] for p in plan['pages'])}", "done", model)

        bodies = _generate_pages(session, plan, [(page, None, None) for page in plan["pages"]])
        version = projects.write_version(pid, render_site(plan, bodies, pid), "Initial build")
        session.log(f"Version {version} is live", "done")
        session.say(
            f"I built {plan['app_name']} with {len(plan['pages'])} pages: "
            f"{', '.join(p['title'] for p in plan['pages'])}. Click around the preview, then tell me what to change."
        )
        session.update(status="ready")
    except llm.LLMError as error:
        session.log("Every AI provider failed. Wait a minute and retry.", "error")
        session.update(status="error", error=str(error))


def _apply_changes(plan: dict, decision: dict, image_count: int) -> tuple[dict, list[str], list[tuple[str, str]], list[str]]:
    """Returns (new plan, added slugs, (slug, instruction) edits, notes)."""
    notes = []
    raw = json.loads(json.dumps(plan))
    if decision.get("app_name"):
        raw["app_name"] = decision["app_name"]
        notes.append("renamed the app")
    if isinstance(decision.get("theme"), dict) and decision["theme"]:
        raw["theme"].update(decision["theme"])
        notes.append("updated the theme")
    if decision.get("layout") in ("topnav", "sidebar") and decision["layout"] != plan["layout"]:
        raw["layout"] = decision["layout"]
        notes.append(f"switched to {decision['layout']} layout")

    existing = {page["slug"] for page in plan["pages"]}
    removed = {_slug(s) for s in decision.get("remove_pages") or []} & existing - {"index"}
    raw["pages"] = [page for page in raw["pages"] if page["slug"] not in removed]
    if removed:
        notes.append("removed " + ", ".join(sorted(removed)))

    added = []
    for page in decision.get("add_pages") or []:
        if isinstance(page, dict) and _slug(page.get("slug") or page.get("title")) not in existing:
            raw["pages"].append(page)
            added.append(_slug(page.get("slug") or page.get("title")))

    known_keys = {item.get("key") for item in raw["state"]}
    for item in decision.get("state_additions") or []:
        if isinstance(item, dict) and item.get("key") and item["key"] not in known_keys:
            raw["state"].append(item)
    badges = decision.get("badges") if isinstance(decision.get("badges"), dict) else {}
    for page in raw["pages"]:
        slug = _slug(page.get("slug") or page.get("title"))
        if badges.get(slug):
            page["badge"] = badges[slug]
            notes.append(f"added a count badge to {page.get('title') or slug}")

    for name, records in (decision.get("data_additions") or {}).items():
        if isinstance(records, list):
            raw["data"].setdefault(name, []).extend(r for r in records if isinstance(r, dict))

    new_plan = normalize_plan(raw, image_count)
    slugs = {page["slug"] for page in new_plan["pages"]}
    added = [slug for slug in added if slug in slugs]
    edits = []
    for item in decision.get("edit_pages") or []:
        if isinstance(item, dict):
            slug = _slug(item.get("slug"))
            if slug in slugs and slug not in added and item.get("instruction"):
                edits.append((slug, str(item["instruction"])))
    return new_plan, added, edits, notes


def _chat(pid: str, message: str):
    session = _Session(pid)
    project = session.project
    projects.add_message(pid, "user", message)
    project["chat"].append({"role": "user", "content": message})
    session.update(status="building")

    if not project["current"]:  # nothing built yet: fold the message into the brief and build
        projects.update(pid, prompt=(project["prompt"] + "\n" + message).strip())
        return _build(pid)

    files = projects.read_version(pid, project["current"])
    plan = json.loads(files["_src/plan.json"])
    if plan.get("kind") == "react":
        import react_builder

        try:
            react_builder.chat(session, message, files)
        except llm.LLMError:
            session.log("Every AI provider failed for this change. Wait a minute and try again.", "error")
            session.say("Sorry, all the AI providers are busy right now. Please send that again in a minute.")
        session.update(status="ready")
        return
    bodies = {page["slug"]: files.get(f"_src/pages/{page['slug']}.html", "") for page in plan["pages"]}
    try:
        session.log("Working out what to change", "step")
        decision, model = llm.complete(
            "chat", ROUTER_SYSTEM, router_prompt(project, plan, message), json_mode=True, max_tokens=4000,
            validate=llm.parse_json, on_fallback=session.fallback("Chat"),
        )
        plan, added, edits, notes = _apply_changes(plan, decision, len(project["images"]))
        session.say(str(decision.get("reply") or "On it."))

        if not (added or edits or notes):
            session.log("No changes needed", "done", model)
            session.update(status="ready")
            return

        by_slug = {page["slug"]: page for page in plan["pages"]}
        jobs = [(by_slug[slug], None, None) for slug in added]
        jobs += [(by_slug[slug], instruction, bodies.get(slug) or placeholder_body(by_slug[slug])) for slug, instruction in edits]
        bodies.update(_generate_pages(session, plan, jobs) if jobs else {})

        changed = notes + [f"added {by_slug[s]['title']}" for s in added] + [f"updated {by_slug[s]['title']}" for s, _ in edits]
        version = projects.write_version(pid, render_site(plan, bodies, pid), message[:80])
        session.log(f"Version {version} is live: {', '.join(changed)}", "done")
        session.update(status="ready", name=plan["app_name"])
    except llm.LLMError:
        session.log("Every AI provider failed for this change. Wait a minute and try again.", "error")
        session.say("Sorry, all the AI providers are busy right now. Please send that again in a minute.")
        session.update(status="ready")


def _restore(pid: str, version: int):
    session = _Session(pid)
    files = projects.read_version(pid, version)
    plan = json.loads(files["_src/plan.json"])
    number = projects.write_version(pid, files, f"Restored version {version}")
    session.log(f"Restored version {version} as version {number}", "done")
    session.update(status="ready", name=plan["app_name"])
