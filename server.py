"""NaturalSketch web server.

Run with:  python server.py   then open http://localhost:8000
"""

import io
import json
import logging
import mimetypes
import os
import time
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
from starlette.concurrency import run_in_threadpool

load_dotenv(Path(__file__).parent / ".env")

import auth  # noqa: E402  (these need the environment loaded first)
import builder  # noqa: E402
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
# The builder and the generated apps live on different origins, so code written by the AI can
# never read the builder's pages or call its API as the signed-in user. Deployed, the builder is
# NS_APP_ORIGIN (e.g. the Vercel domain) and the generated apps are served from NS_PREVIEW_ORIGIN.
APP_ORIGIN = os.environ.get("NS_APP_ORIGIN", f"http://localhost:{PORT}").rstrip("/")
PREVIEW_ORIGIN = os.environ.get("NS_PREVIEW_ORIGIN", f"http://127.0.0.1:{PORT}").rstrip("/")
APP_HOST = urlsplit(APP_ORIGIN).hostname
PREVIEW_HOST = urlsplit(PREVIEW_ORIGIN).hostname
SESSION_COOKIE = "ns_session"
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

# API paths anyone may call, signed in or not.
OPEN_API = {"/api/status", "/api/session"}


@app.middleware("http")
async def split_origins(request: Request, call_next):
    host, path = request.url.hostname, request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    if path == "/healthz":
        return await call_next(request)
    if path.startswith("/preview/"):
        if host != PREVIEW_HOST:
            return RedirectResponse(f"{PREVIEW_ORIGIN}{path}{query}")
        return await call_next(request)
    is_api = path.startswith("/api/")
    if host == PREVIEW_HOST and not is_api:
        return RedirectResponse(f"{APP_ORIGIN}{path}{query}")
    if is_api and path not in OPEN_API:
        if auth.ENABLED:
            # The session cookie lives on the builder's domain only, so generated apps (served
            # from the preview domain) can never call the API as the user.
            user = await run_in_threadpool(auth.verify, _token(request))
            if not user:
                return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
            request.state.user = user
        elif host == PREVIEW_HOST:
            return JSONResponse({"detail": "Not available from generated apps"}, status_code=403)
        else:
            request.state.user = auth.LOCAL_USER
    return await call_next(request)


def _token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def _user(request: Request) -> dict:
    return request.state.user


def _project(pid: str, user: dict, full: bool = True) -> dict | None:
    """The project if it belongs to this user; 404 otherwise (also for other people's projects)."""
    try:
        owner = projects.owner_of(pid)
        if user["id"] is not None and owner != user["id"]:
            raise KeyError(pid)
        return projects.load(pid) if full else None
    except KeyError:
        raise HTTPException(404, "Project not found") from None


def _busy(pid: str) -> None:
    if builder.is_running(pid):
        raise HTTPException(409, "This project is still building. Wait for it to finish.")


def _spend(user: dict) -> None:
    """Count a build or chat edit against the account's daily allowance."""
    if user["id"] is None or user["admin"] or auth.DAILY_LIMIT <= 0:
        return
    if not projects.take_action(user["id"], auth.DAILY_LIMIT):
        raise HTTPException(
            429, f"You've used all {auth.DAILY_LIMIT} builds and edits for today. Your allowance resets at midnight UTC.",
        )


def _usage(user: dict) -> dict:
    unlimited = user["id"] is None or user["admin"] or auth.DAILY_LIMIT <= 0
    return {"used": 0 if user["id"] is None else projects.actions_today(user["id"]), "limit": None if unlimited else auth.DAILY_LIMIT}


def _public(user: dict) -> dict:
    return {k: user[k] for k in ("email", "name", "avatar", "admin")}


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/privacy.html")
def privacy() -> FileResponse:
    return FileResponse(WEB_DIR / "privacy.html")


@app.get("/terms.html")
def terms() -> FileResponse:
    return FileResponse(WEB_DIR / "terms.html")


@app.get("/api/status")
def status() -> dict:
    return {**llm.status(), "preview_origin": PREVIEW_ORIGIN, "auth": auth.public_config()}


# ------------------------------------------------------------------ accounts

class Session(BaseModel):
    access_token: str


@app.post("/api/session")
def start_session(body: Session) -> Response:
    """Called by the browser after sign-in and on every token refresh."""
    if not auth.ENABLED:
        return JSONResponse({"user": _public(auth.LOCAL_USER)})
    user = auth.verify(body.access_token)
    if not user:
        raise HTTPException(401, "Your sign-in has expired. Please sign in again.")
    if user["admin"]:
        claimed = projects.claim_unowned(user["id"])
        if claimed:
            logging.info("Gave %d existing projects to %s", claimed, user["email"])
    response = JSONResponse({"user": _public(user), "usage": _usage(user)})
    response.set_cookie(
        SESSION_COOKIE, body.access_token, max_age=max(60, user["exp"] - int(time.time())),
        httponly=True, samesite="lax", secure=APP_ORIGIN.startswith("https://"), path="/",
    )
    return response


@app.delete("/api/session")
def end_session() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def me(request: Request) -> dict:
    user = _user(request)
    return {"user": _public(user), "usage": _usage(user)}


# ------------------------------------------------------------------ projects

@app.get("/api/projects")
def list_projects(request: Request) -> list[dict]:
    return projects.list_all(_user(request)["id"])


@app.post("/api/projects")
async def create_project(
    request: Request,
    prompt: str = Form(""),
    features: str = Form(""),
    images: list[UploadFile] = File(default=[]),
) -> dict:
    user = _user(request)
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

    await run_in_threadpool(_spend, user)
    pid = projects.create(prompt, feature_list, stored, owner=user["id"])
    builder.start_build(pid)
    return {"id": pid}


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request) -> dict:
    project = _project(pid, _user(request))
    project["running"] = builder.is_running(pid)
    return project


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, request: Request) -> dict:
    _project(pid, _user(request), full=False)
    _busy(pid)
    projects.delete(pid)
    return {"ok": True}


class ChatMessage(BaseModel):
    message: str


@app.post("/api/projects/{pid}/chat")
def chat(pid: str, body: ChatMessage, request: Request) -> dict:
    user = _user(request)
    _project(pid, user, full=False)
    message = body.message.strip()[:4000]
    if not message:
        raise HTTPException(400, "Write a message first.")
    _busy(pid)
    _spend(user)
    if not builder.start_chat(pid, message):
        _busy(pid)
    return {"ok": True}


@app.post("/api/projects/{pid}/rebuild")
def rebuild(pid: str, request: Request) -> dict:
    user = _user(request)
    _project(pid, user, full=False)
    _busy(pid)
    _spend(user)
    if not builder.start_build(pid):
        _busy(pid)
    return {"ok": True}


class Restore(BaseModel):
    version: int


@app.post("/api/projects/{pid}/restore")
def restore(pid: str, body: Restore, request: Request) -> dict:
    project = _project(pid, _user(request))
    if body.version not in {v["n"] for v in project["versions"]}:
        raise HTTPException(404, "Version not found")
    if not builder.start_restore(pid, body.version):
        _busy(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/uploads/{name}")
def upload(pid: str, name: str, request: Request) -> Response:
    if not projects.valid_id(pid) or not name.isdigit():
        raise HTTPException(404)
    _project(pid, _user(request), full=False)
    found = projects.read_upload(pid, int(name))
    if not found:
        raise HTTPException(404)
    data, mime = found
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/projects/{pid}/download")
def download(pid: str, request: Request, version: int | None = None) -> Response:
    project = _project(pid, _user(request))
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
    # Previews are reachable by their (unguessable) project id, like a share link.
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
    print(f"NaturalSketch is running at {APP_ORIGIN}" + (" (accounts on)" if auth.ENABLED else " (local single-user mode)"))
    uvicorn.run(app, host=HOST, port=PORT, proxy_headers=True, forwarded_allow_ips="*")
