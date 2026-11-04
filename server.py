"""NaturalSketch web server.

Run with:  python server.py   then open http://localhost:8000
"""

import base64
import hmac
import io
import json
import logging
import mimetypes
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

import builder  # noqa: E402  (needs the environment loaded first)
import llm  # noqa: E402
import projects  # noqa: E402
import vision_processor  # noqa: E402

mimetypes.add_type("text/javascript", ".js")  # Windows may map .js to text/plain
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

WEB_DIR = Path(__file__).parent / "web"
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 10 * 1024 * 1024
PORT = int(os.environ.get("PORT", "8000"))
# Locally the server only listens on this computer. A host sets HOST=0.0.0.0.
HOST = os.environ.get("HOST", "127.0.0.1")
# The builder and the generated apps live on different origins (same server), so code
# written by the AI can never read the builder's pages or call its API. When deployed, point
# two domains at the server, e.g. NS_APP_ORIGIN=https://naturalsketch.example.com and
# NS_PREVIEW_ORIGIN=https://naturalsketch-apps.example.com
APP_ORIGIN = os.environ.get("NS_APP_ORIGIN", f"http://localhost:{PORT}").rstrip("/")
PREVIEW_ORIGIN = os.environ.get("NS_PREVIEW_ORIGIN", f"http://127.0.0.1:{PORT}").rstrip("/")
APP_HOST = urlsplit(APP_ORIGIN).hostname
PREVIEW_HOST = urlsplit(PREVIEW_ORIGIN).hostname
# Optional password for the builder (strongly recommended on a public server: without it anyone
# can spend your API quota and delete projects). The browser asks for it; any username works.
ACCESS_PASSWORD = os.environ.get("NS_ACCESS_PASSWORD", "")
TEXT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
}

@asynccontextmanager
async def lifespan(_app: FastAPI):
    projects.init()  # connects to Supabase and creates the tables on first run
    projects.mark_interrupted()
    yield
    projects.close()


app = FastAPI(title="NaturalSketch", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def split_origins(request: Request, call_next):
    host, path = request.url.hostname, request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    if path.startswith("/preview/"):
        if host != PREVIEW_HOST:
            return RedirectResponse(f"{PREVIEW_ORIGIN}{path}{query}")
    elif host == PREVIEW_HOST:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Not available from generated apps"}, status_code=403)
        return RedirectResponse(f"{APP_ORIGIN}{path}{query}")
    elif ACCESS_PASSWORD and not _authorized(request):
        return Response("Password required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="NaturalSketch"'})
    return await call_next(request)


def _authorized(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        _, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(password, ACCESS_PASSWORD)


def _project(pid: str) -> dict:
    try:
        return projects.load(pid)
    except KeyError:
        raise HTTPException(404, "Project not found") from None


def _busy(pid: str) -> None:
    if builder.is_running(pid):
        raise HTTPException(409, "This project is still building. Wait for it to finish.")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {**llm.status(), "preview_origin": PREVIEW_ORIGIN}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return projects.list_all()


@app.post("/api/projects")
async def create_project(
    prompt: str = Form(""),
    features: str = Form(""),
    images: list[UploadFile] = File(default=[]),
) -> dict:
    prompt = prompt.strip()[:4000]
    feature_list = [line.strip(" -•*\t") for line in features.splitlines() if line.strip(" -•*\t")][:40]
    images = [image for image in images if image.filename]
    if not prompt and not images:
        raise HTTPException(400, "Add a sketch or describe your app.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"Upload at most {MAX_IMAGES} images.")

    stored = []
    for upload in images:
        data = await upload.read()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(400, f"{upload.filename} is larger than 10 MB.")
        try:
            kind = vision_processor.classify(data)
            data = vision_processor.normalize_upload(data)
        except Exception:
            raise HTTPException(400, f"{upload.filename} is not a readable image.") from None
        stored.append((data, "image/jpeg", kind))

    pid = projects.create(prompt, feature_list, stored)
    builder.start_build(pid)
    return {"id": pid}


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    project = _project(pid)
    project["running"] = builder.is_running(pid)
    return project


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict:
    _project(pid)
    _busy(pid)
    projects.delete(pid)
    return {"ok": True}


class ChatMessage(BaseModel):
    message: str


@app.post("/api/projects/{pid}/chat")
def chat(pid: str, body: ChatMessage) -> dict:
    _project(pid)
    message = body.message.strip()[:4000]
    if not message:
        raise HTTPException(400, "Write a message first.")
    if not builder.start_chat(pid, message):
        _busy(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/rebuild")
def rebuild(pid: str) -> dict:
    _project(pid)
    if not builder.start_build(pid):
        _busy(pid)
    return {"ok": True}


class Restore(BaseModel):
    version: int


@app.post("/api/projects/{pid}/restore")
def restore(pid: str, body: Restore) -> dict:
    project = _project(pid)
    if body.version not in {v["n"] for v in project["versions"]}:
        raise HTTPException(404, "Version not found")
    if not builder.start_restore(pid, body.version):
        _busy(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/uploads/{name}")
def upload(pid: str, name: str) -> Response:
    if not projects.valid_id(pid) or not name.isdigit():
        raise HTTPException(404)
    found = projects.read_upload(pid, int(name))
    if not found:
        raise HTTPException(404)
    data, mime = found
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/projects/{pid}/download")
def download(pid: str, version: int | None = None) -> Response:
    project = _project(pid)
    version = version or project["current"]
    if version not in {v["n"] for v in project["versions"]}:
        raise HTTPException(404, "Nothing built yet")
    files = projects.read_version(pid, version)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            if not path.startswith("_src/"):
                archive.writestr(path, content)
        archive.writestr("README.txt", (
            f"{project['name']}: generated by NaturalSketch (version {version}).\n\n"
            "Open index.html in a browser, or upload this folder to any static host\n"
            "(Netlify Drop, GitHub Pages, Vercel...). Data and logins are stored in the\n"
            "browser's localStorage, so it works without a backend.\n"
        ))
        archive.writestr("plan.json", json.dumps(json.loads(files["_src/plan.json"]), indent=2))
    slug = builder._slug(project["name"])
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}-v{version}.zip"'},
    )


@app.get("/preview/{pid}/{version}/{path:path}")
def preview(pid: str, version: int, path: str = "") -> Response:
    path = path or "index.html"
    if not projects.valid_id(pid) or path.startswith("_src/"):
        raise HTTPException(404)
    content = projects.read_file(pid, version, path)
    if content is None:
        raise HTTPException(404, "Page not found")
    suffix = "." + path.rsplit(".", 1)[-1] if "." in path else ""
    media_type = TEXT_TYPES.get(suffix) or mimetypes.guess_type(path)[0] or "text/plain"
    return Response(
        content,
        media_type=media_type,
        headers={"Access-Control-Allow-Origin": APP_ORIGIN, "Cache-Control": "no-cache"},
    )


if __name__ == "__main__":
    # By default bound to this computer only: the API keys and their free quota stay private.
    print(f"NaturalSketch is running at {APP_ORIGIN}")
    uvicorn.run(app, host=HOST, port=PORT, proxy_headers=True, forwarded_allow_ips="*")
