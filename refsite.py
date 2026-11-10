"""Reference websites: fetch a page the user pointed at and summarise its structure and style.

Only public http(s) pages are fetched (private and local addresses are refused, also after
redirects), with small size and time limits. The summary (headings, navigation, colours, fonts)
is given to the planner as inspiration; the generated site gets its own copy and branding.
"""

import ipaddress
import re
import socket
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REFS = 3
MAX_BYTES = 700_000
TIMEOUT = 12
USER_AGENT = "Mozilla/5.0 (compatible; NaturalSketchBot/1.0; reference preview)"
URL_RE = re.compile(r"^https?://[^\s<>\"']{3,400}$", re.IGNORECASE)


def parse_urls(text: str) -> list[str]:
    """Up to MAX_REFS distinct http(s) URLs from free text (one per line or space separated)."""
    urls = []
    for token in re.split(r"[\s,]+", text or ""):
        token = token.strip()
        if token and "://" not in token and "." in token and not token.startswith("/"):
            token = "https://" + token
        if URL_RE.match(token) and token not in urls:
            urls.append(token)
    return urls[:MAX_REFS]


def _public_host(host: str) -> bool:
    """True if every address the host resolves to is a public internet address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            return False
    return bool(infos)


def _get(client: httpx.Client, url: str) -> tuple[str, str]:
    """Fetch text from a public URL, following up to 4 checked redirects. Returns (final url, text)."""
    for _ in range(5):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.port not in (None, 80, 443):
            raise ValueError("only public http(s) addresses can be read")
        if not _public_host(parts.hostname):
            raise ValueError("that address is not a public website")
        with client.stream("GET", url) as response:
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            if response.status_code >= 400:
                raise ValueError(f"the site answered with HTTP {response.status_code}")
            kind = response.headers.get("content-type", "")
            if not any(k in kind for k in ("text/", "css", "html", "xml")):
                raise ValueError("that address is not a web page")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            return url, b"".join(chunks)[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace")
    raise ValueError("too many redirects")


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta = {}
        self.headings, self.nav, self.buttons, self.text = [], [], [], []
        self.stylesheets, self.font_links = [], []
        self.inline_css = []
        self.sections = Counter()
        self._stack = []
        self._skip = 0
        self._in_nav = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "noscript", "svg", "template"):
            self._skip += 1
        if tag == "nav" or (a.get("role") == "navigation"):
            self._in_nav += 1
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:site_name", "og:title", "theme-color") and a.get("content"):
                self.meta[key] = a["content"][:300]
        if tag == "link" and a.get("href"):
            rel = (a.get("rel") or "").lower()
            if "fonts.googleapis.com" in a["href"]:
                self.font_links.append(a["href"])
            elif "stylesheet" in rel:
                self.stylesheets.append(a["href"])
        if tag in ("header", "main", "footer", "section", "article", "aside", "form", "table", "video", "canvas"):
            self.sections[tag] += 1
        if a.get("style"):
            self.inline_css.append(a["style"])
        self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "noscript", "svg", "template") and self._skip:
            self._skip -= 1
        if tag == "nav" and self._in_nav:
            self._in_nav -= 1
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        current = self._stack[-1] if self._stack else ""
        if current == "style":
            self.inline_css.append(data)
            return
        text = " ".join(data.split())
        if not text or self._skip:
            return
        if current == "title":
            self.title = (self.title + " " + text).strip()[:160]
        elif any(t in self._stack[-3:] for t in ("h1", "h2", "h3")):
            self.headings.append(text[:120])
        elif self._in_nav and "a" in self._stack[-3:]:
            self.nav.append(text[:40])
        elif "button" in self._stack[-2:] or (current == "a" and len(text) < 28):
            self.buttons.append(text[:40])
        elif len(text) > 40:
            self.text.append(text)


HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
FONT = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
GENERIC_FONTS = {"sans-serif", "serif", "monospace", "system-ui", "inherit", "initial", "ui-sans-serif", "ui-serif",
                 "ui-monospace", "cursive", "-apple-system", "blinkmacsystemfont", "segoe ui", "arial", "helvetica",
                 "helvetica neue", "roboto", "times new roman", "courier new", "apple color emoji", "segoe ui emoji",
                 "noto color emoji", "segoe ui symbol", "var"}


def _colours(css: str) -> list[str]:
    counts = Counter()
    for value in HEX.findall(css):
        value = value.lower()
        if len(value) == 3:
            value = "".join(c * 2 for c in value)
        counts["#" + value] += 1
    return [colour for colour, _ in counts.most_common(8)]


def _fonts(css: str, font_links: list[str]) -> list[str]:
    counts = Counter()
    for link in font_links:
        for family in re.findall(r"family=([^&:]+)", link):
            counts[family.replace("+", " ").strip()] += 5
    for declaration in FONT.findall(css):
        first = declaration.split(",")[0].strip().strip("'\"")
        if first and first.lower() not in GENERIC_FONTS and not first.startswith(("var(", "-")) and len(first) < 40:
            counts[first] += 1
    return [font for font, _ in counts.most_common(4)]


def _unique(items: list[str], limit: int) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out[:limit]


def summarise(url: str) -> dict:
    """Fetch a page and describe its structure and style. Raises ValueError with a readable reason."""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client:
            final, html = _get(client, url)
            page = _Page()
            page.feed(html)
            css = "\n".join(page.inline_css)
            for href in page.stylesheets[:2]:  # the main stylesheets carry the palette and fonts
                try:
                    css += "\n" + _get(client, urljoin(final, href))[1]
                except (ValueError, httpx.HTTPError):
                    pass
    except httpx.HTTPError as error:
        raise ValueError(f"couldn't reach the site ({type(error).__name__})") from None

    colours = _colours(css)
    if page.meta.get("theme-color", "").startswith("#"):
        colours = _unique([page.meta["theme-color"].lower()] + colours, 8)
    summary = {
        "url": final,
        "title": page.meta.get("og:site_name") or page.title,
        "description": page.meta.get("description", ""),
        "headings": _unique(page.headings, 12),
        "navigation": _unique(page.nav, 10),
        "calls_to_action": _unique(page.buttons, 8),
        "layout": ", ".join(f"{count} {tag}" for tag, count in page.sections.most_common(6)),
        "colours": colours,
        "fonts": _fonts(css, page.font_links),
        "sample_text": " ".join(page.text)[:500],
    }
    if not (summary["headings"] or summary["sample_text"] or summary["colours"]):
        raise ValueError("the page has no readable content (it may be built entirely with JavaScript)")
    return summary


def describe(refs: list[dict]) -> str:
    """The references as text for the planner prompt."""
    lines = []
    for ref in refs or []:
        s = ref.get("summary")
        if not s:
            continue
        lines.append(
            f"- {s['url']}: \"{s['title']}\". {s['description']}\n"
            f"  Sections/headings: {'; '.join(s['headings']) or '(none found)'}\n"
            f"  Navigation: {', '.join(s['navigation']) or '(none found)'}. Buttons: {', '.join(s['calls_to_action']) or '(none)'}\n"
            f"  Page structure: {s['layout'] or '(unknown)'}\n"
            f"  Colours used most: {', '.join(s['colours']) or '(unknown)'}. Fonts: {', '.join(s['fonts']) or '(unknown)'}"
        )
    if not lines:
        return "(none)"
    return (
        "\n".join(lines)
        + "\nUse these as inspiration for structure, layout, palette and typography. Write original copy and a new "
          "brand: do not copy their text, names or logos."
    )
