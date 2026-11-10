"""Backends for full-stack projects, in Python (FastAPI), Node (Express) or Java (no dependencies).

All three serve the same REST API, driven by `schema.json` (the resources and field types taken
from the plan's data model) and `seed.json` (the sample records):

    GET    /api/health
    GET    /api/<resource>            list
    GET    /api/<resource>/<id>       one record
    POST   /api/<resource>            create (validated against the schema)
    PUT    /api/<resource>/<id>       update (PATCH also works)
    DELETE /api/<resource>/<id>
    POST   /api/auth/login            sign in, or create the account on first use
    GET    /api/auth/me

The code is written here rather than by the AI, and each language was run against the same test
calls, so an exported project starts first time. The React frontend talks to it through
`src/lib/store.js`.
"""

import json

LANGUAGES = {
    "python": {"label": "Python (FastAPI)", "run": "pip install -r requirements.txt\nuvicorn main:app --reload --port 8000"},
    "node": {"label": "Node.js (Express)", "run": "npm install\nnpm start"},
    "java": {"label": "Java (built-in HTTP server, no dependencies)",
             "run": "mkdir out\njavac -d out src/*.java\njava -cp out Main"},
}


def field_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def schema_for(data: dict) -> dict:
    """Resources and field types, inferred from the sample records."""
    resources = {}
    for name, records in data.items():
        fields = {}
        for record in records:
            for key, value in record.items():
                if key != "id" and value is not None and key not in fields:
                    fields[key] = field_type(value)
        resources[name] = {"fields": fields}
    return {"resources": resources}


# ---------------------------------------------------------------- Python (FastAPI)

PY_MAIN = '''"""__APP__ API (FastAPI).

Run:  pip install -r requirements.txt
      uvicorn main:app --reload --port 8000
Docs: http://localhost:8000/docs
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import auth, resources, store

app = FastAPI(title="__APP__ API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
store.init()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "resources": list(store.SCHEMA)}


app.include_router(auth.router)
app.include_router(resources.router)
'''

PY_STORE = '''"""Storage: a small document store on SQLite. Each resource is a set of JSON records."""

import json
import os
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("DATABASE_FILE", str(BASE / "data.sqlite3"))
SCHEMA = json.loads((BASE / "schema.json").read_text(encoding="utf-8"))["resources"]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    """Create the tables and load seed.json the first time the server starts."""
    with connect() as conn:
        conn.execute(
            "create table if not exists documents (resource text not null, id text not null, "
            "data text not null, created real not null, primary key (resource, id))"
        )
        conn.execute(
            "create table if not exists users (email text primary key, name text not null, "
            "password text, created real not null)"
        )
        if conn.execute("select count(*) from documents").fetchone()[0] == 0:
            seed = json.loads((BASE / "seed.json").read_text(encoding="utf-8"))
            for resource, rows in seed.items():
                for index, row in enumerate(rows):
                    conn.execute(
                        "insert or ignore into documents values (?, ?, ?, ?)",
                        (resource, str(row["id"]), json.dumps(row), time.time() + index / 1000),
                    )


def list_all(resource: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("select data from documents where resource = ? order by created", (resource,)).fetchall()
    return [json.loads(row["data"]) for row in rows]


def get(resource: str, record_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("select data from documents where resource = ? and id = ?", (resource, record_id)).fetchone()
    return json.loads(row["data"]) if row else None


def save(resource: str, record: dict) -> dict:
    with connect() as conn:
        conn.execute(
            "insert into documents values (?, ?, ?, ?) "
            "on conflict (resource, id) do update set data = excluded.data",
            (resource, record["id"], json.dumps(record), time.time()),
        )
    return record


def delete(resource: str, record_id: str) -> bool:
    with connect() as conn:
        return conn.execute("delete from documents where resource = ? and id = ?", (resource, record_id)).rowcount > 0
'''

PY_AUTH = '''"""Sign-in: accounts with hashed passwords and signed tokens (standard library only)."""

import base64
import hashlib
import hmac
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request

from . import store

router = APIRouter(prefix="/api/auth")
SECRET = os.environ.get("SECRET_KEY", "change-me-in-production").encode()
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "0") == "1"  # set to 1 so only signed-in users can change data
TOKEN_DAYS = 30


def _hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
    return f"{salt}${digest}"


def _token(email: str) -> str:
    payload = base64.urlsafe_b64encode(f"{email}|{int(time.time()) + TOKEN_DAYS * 86400}".encode()).decode()
    return payload + "." + hmac.new(SECRET, payload.encode(), "sha256").hexdigest()


def current_user(request: Request) -> dict | None:
    """The signed-in user for this request's Bearer token, or None."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer ") or "." not in header:
        return None
    payload, _, signature = header[7:].strip().rpartition(".")
    if not hmac.compare_digest(signature, hmac.new(SECRET, payload.encode(), "sha256").hexdigest()):
        return None
    try:
        email, _, expires = base64.urlsafe_b64decode(payload.encode()).decode().rpartition("|")
    except ValueError:
        return None
    if int(expires) < time.time():
        return None
    with store.connect() as conn:
        row = conn.execute("select email, name from users where email = ?", (email,)).fetchone()
    return dict(row) if row else None


def require_writer(request: Request) -> None:
    if REQUIRE_AUTH and not current_user(request):
        raise HTTPException(401, "Sign in to change data.")


async def read_json(request: Request) -> dict:
    """The request body as a JSON object, or a 400/422 error."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "The request body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "Send a JSON object.")
    return body


@router.post("/login")
async def login(request: Request) -> dict:
    """Sign in. The first sign-in with a new email creates the account."""
    body = await read_json(request)
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    if "@" not in email:
        raise HTTPException(422, "Enter a valid email address.")
    with store.connect() as conn:
        row = conn.execute("select email, name, password from users where email = ?", (email,)).fetchone()
        if row is None:
            name = str(body.get("name") or email.split("@")[0])
            conn.execute(
                "insert into users values (?, ?, ?, ?)", (email, name, _hash(password) if password else None, time.time())
            )
        else:
            name = row["name"]
            if row["password"]:
                salt = row["password"].split("$")[0]
                if not hmac.compare_digest(row["password"], _hash(password, salt)):
                    raise HTTPException(401, "Wrong email or password.")
    return {"token": _token(email), "user": {"email": email, "name": name}}


@router.get("/me")
def me(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Not signed in.")
    return user
'''

PY_RESOURCES = '''"""REST endpoints for every resource in schema.json."""

import secrets

from fastapi import APIRouter, HTTPException, Request, Response

from . import auth, store

router = APIRouter(prefix="/api")
CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _resource(name: str) -> dict:
    if name not in store.SCHEMA:
        raise HTTPException(404, f"Unknown resource: {name}")
    return store.SCHEMA[name]


def _validate(name: str, body: dict) -> dict:
    for field, kind in _resource(name)["fields"].items():
        value = body.get(field)
        if value is not None and not CHECKS[kind](value):
            raise HTTPException(422, f"\\"{field}\\" must be a {kind}.")
    return body


@router.get("/{name}")
def list_records(name: str) -> list[dict]:
    _resource(name)
    return store.list_all(name)


@router.get("/{name}/{record_id}")
def get_record(name: str, record_id: str) -> dict:
    _resource(name)
    record = store.get(name, record_id)
    if record is None:
        raise HTTPException(404, "Not found.")
    return record


@router.post("/{name}", status_code=201)
async def create_record(name: str, request: Request) -> dict:
    auth.require_writer(request)
    body = _validate(name, await auth.read_json(request))
    record = {**body, "id": str(body.get("id") or secrets.token_hex(6))}
    if store.get(name, record["id"]):
        raise HTTPException(409, "A record with this id already exists.")
    return store.save(name, record)


@router.put("/{name}/{record_id}")
@router.patch("/{name}/{record_id}")
async def update_record(name: str, record_id: str, request: Request) -> dict:
    auth.require_writer(request)
    current = store.get(name, record_id)
    if current is None:
        raise HTTPException(404, "Not found.")
    body = _validate(name, await auth.read_json(request))
    return store.save(name, {**current, **body, "id": record_id})


@router.delete("/{name}/{record_id}", status_code=204)
def delete_record(name: str, record_id: str, request: Request) -> Response:
    auth.require_writer(request)
    _resource(name)
    if not store.delete(name, record_id):
        raise HTTPException(404, "Not found.")
    return Response(status_code=204)
'''

PY_DOCKER = '''FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
'''

# ---------------------------------------------------------------- Node (Express)

NODE_SERVER = '''// __APP__ API (Express).
// Run:  npm install && npm start      (http://localhost:8000)
import cors from "cors";
import express from "express";
import { authRouter } from "./auth.js";
import { resourceRouter } from "./resources.js";
import { schema } from "./store.js";

const app = express();
const origins = process.env.CORS_ORIGINS || "*"; // "*" for any frontend, or a comma-separated list
app.use(cors({ origin: origins === "*" ? "*" : origins.split(",") }));
app.use(express.json({ limit: "2mb" }));

app.get("/api/health", (req, res) => res.json({ ok: true, resources: Object.keys(schema) }));
app.use("/api/auth", authRouter);
app.use("/api", resourceRouter);

// Errors thrown in routes become JSON: { detail: "..." }
app.use((error, req, res, next) => {
  const status = error.status || (error.type === "entity.parse.failed" ? 400 : 500);
  res.status(status).json({ detail: status === 500 ? "Something went wrong." : error.message });
});

const port = Number(process.env.PORT || 8000);
app.listen(port, () => console.log(`__APP__ API listening on http://localhost:${port}`));
'''

NODE_STORE = '''// Storage: JSON records kept in one file (data.json), loaded from seed.json on first run.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const base = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const file = process.env.DATA_FILE || path.join(base, "data.json");
const readJson = (name) => JSON.parse(fs.readFileSync(path.join(base, name), "utf8"));

export const schema = readJson("schema.json").resources;

let state;
if (fs.existsSync(file)) {
  state = JSON.parse(fs.readFileSync(file, "utf8"));
} else {
  const seed = readJson("seed.json");
  state = { documents: {}, users: {} };
  for (const [resource, rows] of Object.entries(seed)) {
    state.documents[resource] = rows.map((row) => ({ ...row, id: String(row.id) }));
  }
  persist();
}

function persist() {
  fs.writeFileSync(file + ".tmp", JSON.stringify(state, null, 2));
  fs.renameSync(file + ".tmp", file);
}

const rows = (resource) => (state.documents[resource] ||= []);

export const store = {
  list: (resource) => rows(resource),
  get: (resource, id) => rows(resource).find((r) => r.id === id) || null,
  save(resource, record) {
    const list = rows(resource);
    const index = list.findIndex((r) => r.id === record.id);
    if (index === -1) list.push(record);
    else list[index] = record;
    persist();
    return record;
  },
  remove(resource, id) {
    const list = rows(resource);
    const index = list.findIndex((r) => r.id === id);
    if (index === -1) return false;
    list.splice(index, 1);
    persist();
    return true;
  },
  user: (email) => state.users[email] || null,
  saveUser(user) {
    state.users[user.email] = user;
    persist();
  },
};
'''

NODE_AUTH = '''// Sign-in: accounts with hashed passwords and signed tokens (Node's crypto module only).
import crypto from "node:crypto";
import { Router } from "express";
import { store } from "./store.js";

const SECRET = process.env.SECRET_KEY || "change-me-in-production";
const REQUIRE_AUTH = process.env.REQUIRE_AUTH === "1"; // set to 1 so only signed-in users can change data
const TOKEN_DAYS = 30;

const fail = (status, message) => Object.assign(new Error(message), { status });
const sign = (payload) => crypto.createHmac("sha256", SECRET).update(payload).digest("hex");

function hash(password, salt = crypto.randomBytes(8).toString("hex")) {
  return `${salt}$${crypto.pbkdf2Sync(password, salt, 100000, 32, "sha256").toString("hex")}`;
}

function token(email) {
  const expires = Math.floor(Date.now() / 1000) + TOKEN_DAYS * 86400;
  const payload = Buffer.from(`${email}|${expires}`).toString("base64url");
  return `${payload}.${sign(payload)}`;
}

export function currentUser(req) {
  const header = req.headers.authorization || "";
  if (!header.toLowerCase().startsWith("bearer ") || !header.includes(".")) return null;
  const value = header.slice(7).trim();
  const dot = value.lastIndexOf(".");
  const payload = value.slice(0, dot);
  const given = Buffer.from(value.slice(dot + 1));
  const expected = Buffer.from(sign(payload));
  if (given.length !== expected.length || !crypto.timingSafeEqual(given, expected)) return null;
  const decoded = Buffer.from(payload, "base64url").toString();
  const bar = decoded.lastIndexOf("|");
  if (bar === -1 || Number(decoded.slice(bar + 1)) < Date.now() / 1000) return null;
  const user = store.user(decoded.slice(0, bar));
  return user ? { email: user.email, name: user.name } : null;
}

export function requireWriter(req, res, next) {
  if (REQUIRE_AUTH && !currentUser(req)) return next(fail(401, "Sign in to change data."));
  next();
}

export const authRouter = Router();

// Sign in. The first sign-in with a new email creates the account.
authRouter.post("/login", (req, res, next) => {
  const body = req.body || {};
  const email = String(body.email || "").trim().toLowerCase();
  const password = String(body.password || "");
  if (!email.includes("@")) return next(fail(422, "Enter a valid email address."));
  let user = store.user(email);
  if (!user) {
    user = { email, name: String(body.name || email.split("@")[0]), password: password ? hash(password) : null };
    store.saveUser(user);
  } else if (user.password && user.password !== hash(password, user.password.split("$")[0])) {
    return next(fail(401, "Wrong email or password."));
  }
  res.json({ token: token(email), user: { email, name: user.name } });
});

authRouter.get("/me", (req, res, next) => {
  const user = currentUser(req);
  if (!user) return next(fail(401, "Not signed in."));
  res.json(user);
});
'''

NODE_RESOURCES = '''// REST endpoints for every resource in schema.json.
import crypto from "node:crypto";
import { Router } from "express";
import { requireWriter } from "./auth.js";
import { schema, store } from "./store.js";

const fail = (status, message) => Object.assign(new Error(message), { status });
const checks = {
  string: (v) => typeof v === "string",
  number: (v) => typeof v === "number",
  boolean: (v) => typeof v === "boolean",
  array: (v) => Array.isArray(v),
  object: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
};

function validate(name, body) {
  if (body === null || typeof body !== "object" || Array.isArray(body)) throw fail(422, "Send a JSON object.");
  for (const [field, kind] of Object.entries(schema[name].fields)) {
    const value = body[field];
    if (value !== undefined && value !== null && !checks[kind](value)) throw fail(422, `"${field}" must be a ${kind}.`);
  }
  return body;
}

export const resourceRouter = Router();

resourceRouter.param("name", (req, res, next, name) => {
  if (!Object.hasOwn(schema, name)) return next(fail(404, `Unknown resource: ${name}`));
  next();
});

resourceRouter.get("/:name", (req, res) => res.json(store.list(req.params.name)));

resourceRouter.get("/:name/:id", (req, res, next) => {
  const record = store.get(req.params.name, req.params.id);
  if (!record) return next(fail(404, "Not found."));
  res.json(record);
});

resourceRouter.post("/:name", requireWriter, (req, res) => {
  const body = validate(req.params.name, req.body);
  const record = { ...body, id: String(body.id || crypto.randomBytes(6).toString("hex")) };
  if (store.get(req.params.name, record.id)) throw fail(409, "A record with this id already exists.");
  res.status(201).json(store.save(req.params.name, record));
});

function update(req, res) {
  const current = store.get(req.params.name, req.params.id);
  if (!current) throw fail(404, "Not found.");
  const body = validate(req.params.name, req.body);
  res.json(store.save(req.params.name, { ...current, ...body, id: req.params.id }));
}
resourceRouter.put("/:name/:id", requireWriter, update);
resourceRouter.patch("/:name/:id", requireWriter, update);

resourceRouter.delete("/:name/:id", requireWriter, (req, res) => {
  if (!store.remove(req.params.name, req.params.id)) throw fail(404, "Not found.");
  res.status(204).end();
});
'''

NODE_DOCKER = '''FROM node:22-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY . .
EXPOSE 8000
CMD ["npm", "start"]
'''

# ---------------------------------------------------------------- Java (no dependencies)

JAVA_MAIN = '''import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * __APP__ API. Uses only the JDK (Java 8 or newer), so there is nothing to install.
 *
 * Run:  mkdir out
 *       javac -d out src/*.java
 *       java -cp out Main            (http://localhost:8000)
 */
public class Main {
    static class ApiError extends RuntimeException {
        private static final long serialVersionUID = 1L;
        final int status;

        ApiError(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    static Store store;
    static Auth auth;

    public static void main(String[] args) throws IOException {
        store = new Store();
        auth = new Auth(store);
        String env = System.getenv("PORT");
        int port = env == null ? 8000 : Integer.parseInt(env);
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/", Main::handle);
        server.setExecutor(Executors.newFixedThreadPool(8));
        server.start();
        System.out.println("__APP__ API listening on http://localhost:" + port);
    }

    static void handle(HttpExchange exchange) throws IOException {
        // CORS_ORIGINS: "*" for any frontend (the default), or a comma-separated list
        String allowed = System.getenv("CORS_ORIGINS");
        String origin = exchange.getRequestHeaders().getFirst("Origin");
        if (allowed == null || allowed.equals("*")) {
            exchange.getResponseHeaders().set("Access-Control-Allow-Origin", "*");
        } else if (origin != null && java.util.Arrays.asList(allowed.split(",")).contains(origin)) {
            exchange.getResponseHeaders().set("Access-Control-Allow-Origin", origin);
            exchange.getResponseHeaders().set("Vary", "Origin");
        }
        exchange.getResponseHeaders().set("Access-Control-Allow-Headers", "Content-Type, Authorization");
        exchange.getResponseHeaders().set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS");
        try {
            if (exchange.getRequestMethod().equals("OPTIONS")) {
                send(exchange, 204, null);
                return;
            }
            Object result = route(exchange);
            int status = exchange.getRequestMethod().equals("POST") && !exchange.getRequestURI().getPath().startsWith("/api/auth")
                    ? 201 : (result == null ? 204 : 200);
            send(exchange, status, result);
        } catch (ApiError error) {
            send(exchange, error.status, detail(error.getMessage()));
        } catch (Json.ParseException error) {
            send(exchange, 400, detail("The request body is not valid JSON."));
        } catch (RuntimeException error) {
            error.printStackTrace();
            send(exchange, 500, detail("Something went wrong."));
        }
    }

    /** Returns the response body (a Map or List), or null for "no content". */
    static Object route(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        List<String> parts = new ArrayList<String>();
        for (String part : exchange.getRequestURI().getPath().split("/")) {
            if (!part.isEmpty()) parts.add(part);
        }
        if (parts.isEmpty() || !parts.get(0).equals("api") || parts.size() < 2 || parts.size() > 3) {
            throw new ApiError(404, "Not found.");
        }
        String name = parts.get(1);
        String id = parts.size() == 3 ? parts.get(2) : null;
        String token = exchange.getRequestHeaders().getFirst("Authorization");

        if (name.equals("health") && id == null) {
            Map<String, Object> health = new LinkedHashMap<String, Object>();
            health.put("ok", true);
            health.put("resources", new ArrayList<String>(store.schema.keySet()));
            return health;
        }
        if (name.equals("auth")) {
            if ("login".equals(id) && method.equals("POST")) return auth.login(body(exchange));
            if ("me".equals(id) && method.equals("GET")) {
                Map<String, Object> user = auth.currentUser(token);
                if (user == null) throw new ApiError(401, "Not signed in.");
                return user;
            }
            throw new ApiError(404, "Not found.");
        }
        if (!store.schema.containsKey(name)) throw new ApiError(404, "Unknown resource: " + name);

        if (method.equals("GET")) {
            if (id == null) return store.list(name);
            Map<String, Object> record = store.get(name, id);
            if (record == null) throw new ApiError(404, "Not found.");
            return record;
        }
        auth.requireWriter(token);
        if (method.equals("POST") && id == null) {
            Map<String, Object> record = new LinkedHashMap<String, Object>(validate(name, body(exchange)));
            Object given = record.get("id");
            record.put("id", given == null ? Auth.randomHex(6) : Json.text(given));
            if (store.get(name, (String) record.get("id")) != null) {
                throw new ApiError(409, "A record with this id already exists.");
            }
            return store.save(name, record);
        }
        if ((method.equals("PUT") || method.equals("PATCH")) && id != null) {
            Map<String, Object> current = store.get(name, id);
            if (current == null) throw new ApiError(404, "Not found.");
            Map<String, Object> record = new LinkedHashMap<String, Object>(current);
            record.putAll(validate(name, body(exchange)));
            record.put("id", id);
            return store.save(name, record);
        }
        if (method.equals("DELETE") && id != null) {
            if (!store.remove(name, id)) throw new ApiError(404, "Not found.");
            return null;
        }
        throw new ApiError(405, "Method not allowed.");
    }

    /** Checks the body's fields against the types in schema.json. */
    static Map<String, Object> validate(String name, Map<String, Object> body) {
        for (Map.Entry<String, String> field : store.schema.get(name).entrySet()) {
            Object value = body.get(field.getKey());
            if (value == null) continue;
            String kind = field.getValue();
            boolean ok = kind.equals("string") ? value instanceof String
                    : kind.equals("number") ? value instanceof Number
                    : kind.equals("boolean") ? value instanceof Boolean
                    : kind.equals("array") ? value instanceof List
                    : value instanceof Map;
            if (!ok) throw new ApiError(422, "\\"" + field.getKey() + "\\" must be a " + kind + ".");
        }
        return body;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> body(HttpExchange exchange) throws IOException {
        InputStream in = exchange.getRequestBody();
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int read;
        while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
        Object value = Json.parse(new String(out.toByteArray(), StandardCharsets.UTF_8));
        if (!(value instanceof Map)) throw new ApiError(422, "Send a JSON object.");
        return (Map<String, Object>) value;
    }

    static Map<String, Object> detail(String message) {
        Map<String, Object> map = new LinkedHashMap<String, Object>();
        map.put("detail", message);
        return map;
    }

    static void send(HttpExchange exchange, int status, Object value) throws IOException {
        if (value == null) {
            exchange.sendResponseHeaders(status, -1);
            exchange.close();
            return;
        }
        byte[] bytes = Json.write(value).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        OutputStream out = exchange.getResponseBody();
        out.write(bytes);
        out.close();
    }
}
'''

JAVA_JSON = '''import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A small JSON reader and writer: objects become Map, arrays List, numbers Long or Double. */
public final class Json {
    public static class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        ParseException(String message) {
            super(message);
        }
    }

    private final String text;
    private int at;

    private Json(String text) {
        this.text = text;
    }

    public static Object parse(String text) {
        Json reader = new Json(text);
        Object value = reader.value();
        reader.space();
        if (reader.at != text.length()) throw new ParseException("Unexpected text after the JSON value");
        return value;
    }

    /** A value as plain text (numbers without a trailing .0). */
    public static String text(Object value) {
        return value instanceof String ? (String) value : write(value);
    }

    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        write(value, out);
        return out.toString();
    }

    private static void write(Object value, StringBuilder out) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            string((String) value, out);
        } else if (value instanceof Double && ((Double) value) == Math.rint((Double) value) && !((Double) value).isInfinite()
                && Math.abs((Double) value) < 1e15) {
            out.append(((Double) value).longValue());
        } else if (value instanceof Number || value instanceof Boolean) {
            out.append(value);
        } else if (value instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                string(String.valueOf(entry.getKey()), out);
                out.append(':');
                write(entry.getValue(), out);
            }
            out.append('}');
        } else if (value instanceof Iterable) {
            out.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) value) {
                if (!first) out.append(',');
                first = false;
                write(item, out);
            }
            out.append(']');
        } else {
            string(String.valueOf(value), out);
        }
    }

    private static void string(String value, StringBuilder out) {
        out.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c == '"') out.append("\\\\\\"");
            else if (c == '\\\\') out.append("\\\\\\\\");
            else if (c == '\\n') out.append("\\\\n");
            else if (c == '\\r') out.append("\\\\r");
            else if (c == '\\t') out.append("\\\\t");
            else if (c < 0x20) out.append(String.format("\\\\u%04x", (int) c));
            else out.append(c);
        }
        out.append('"');
    }

    private void space() {
        while (at < text.length() && Character.isWhitespace(text.charAt(at))) at++;
    }

    private Object value() {
        space();
        if (at >= text.length()) throw new ParseException("Unexpected end of JSON");
        char c = text.charAt(at);
        if (c == '{') return object();
        if (c == '[') return array();
        if (c == '"') return string();
        if (text.startsWith("true", at)) {
            at += 4;
            return Boolean.TRUE;
        }
        if (text.startsWith("false", at)) {
            at += 5;
            return Boolean.FALSE;
        }
        if (text.startsWith("null", at)) {
            at += 4;
            return null;
        }
        return number();
    }

    private Map<String, Object> object() {
        Map<String, Object> map = new LinkedHashMap<String, Object>();
        at++;
        space();
        if (peek('}')) return map;
        while (true) {
            space();
            if (at >= text.length() || text.charAt(at) != '"') throw new ParseException("Expected a property name");
            String key = string();
            space();
            expect(':');
            map.put(key, value());
            space();
            if (peek('}')) return map;
            expect(',');
        }
    }

    private List<Object> array() {
        List<Object> list = new ArrayList<Object>();
        at++;
        space();
        if (peek(']')) return list;
        while (true) {
            list.add(value());
            space();
            if (peek(']')) return list;
            expect(',');
        }
    }

    private boolean peek(char c) {
        if (at < text.length() && text.charAt(at) == c) {
            at++;
            return true;
        }
        return false;
    }

    private void expect(char c) {
        if (!peek(c)) throw new ParseException("Expected '" + c + "' at position " + at);
    }

    private String string() {
        StringBuilder out = new StringBuilder();
        at++;
        while (true) {
            if (at >= text.length()) throw new ParseException("Unterminated string");
            char c = text.charAt(at++);
            if (c == '"') return out.toString();
            if (c != '\\\\') {
                out.append(c);
                continue;
            }
            if (at >= text.length()) throw new ParseException("Unterminated string");
            char e = text.charAt(at++);
            if (e == 'n') out.append('\\n');
            else if (e == 't') out.append('\\t');
            else if (e == 'r') out.append('\\r');
            else if (e == 'b') out.append('\\b');
            else if (e == 'f') out.append('\\f');
            else if (e == 'u') {
                if (at + 4 > text.length()) throw new ParseException("Bad unicode escape");
                out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                at += 4;
            } else out.append(e);
        }
    }

    private Object number() {
        int start = at;
        while (at < text.length() && "+-0123456789.eE".indexOf(text.charAt(at)) != -1) at++;
        String raw = text.substring(start, at);
        if (raw.isEmpty()) throw new ParseException("Unexpected character at position " + start);
        try {
            if (raw.indexOf('.') == -1 && raw.indexOf('e') == -1 && raw.indexOf('E') == -1) return Long.parseLong(raw);
            return Double.parseDouble(raw);
        } catch (NumberFormatException error) {
            throw new ParseException("Bad number: " + raw);
        }
    }
}
'''

JAVA_STORE = '''import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Storage: JSON records kept in one file (data.json), loaded from seed.json on first run. */
public class Store {
    /** resource name -> field name -> type ("string", "number", "boolean", "array", "object") */
    final Map<String, Map<String, String>> schema = new LinkedHashMap<String, Map<String, String>>();
    private final Map<String, List<Map<String, Object>>> documents = new LinkedHashMap<String, List<Map<String, Object>>>();
    private final Map<String, Map<String, Object>> users = new LinkedHashMap<String, Map<String, Object>>();
    private final Path file;

    @SuppressWarnings("unchecked")
    Store() throws IOException {
        String env = System.getenv("DATA_FILE");
        file = Paths.get(env == null ? "data.json" : env);
        Map<String, Object> resources = (Map<String, Object>) ((Map<String, Object>) read("schema.json")).get("resources");
        for (Map.Entry<String, Object> resource : resources.entrySet()) {
            Map<String, String> fields = new LinkedHashMap<String, String>();
            Map<String, Object> raw = (Map<String, Object>) ((Map<String, Object>) resource.getValue()).get("fields");
            for (Map.Entry<String, Object> field : raw.entrySet()) fields.put(field.getKey(), (String) field.getValue());
            schema.put(resource.getKey(), fields);
            documents.put(resource.getKey(), new ArrayList<Map<String, Object>>());
        }
        if (Files.exists(file)) {
            Map<String, Object> state = (Map<String, Object>) Json.parse(new String(Files.readAllBytes(file), StandardCharsets.UTF_8));
            load((Map<String, Object>) state.get("documents"));
            for (Map.Entry<String, Object> user : ((Map<String, Object>) state.get("users")).entrySet()) {
                users.put(user.getKey(), (Map<String, Object>) user.getValue());
            }
        } else {
            load((Map<String, Object>) read("seed.json"));
            persist();
        }
    }

    private static Object read(String name) throws IOException {
        return Json.parse(new String(Files.readAllBytes(Paths.get(name)), StandardCharsets.UTF_8));
    }

    @SuppressWarnings("unchecked")
    private void load(Map<String, Object> source) {
        for (Map.Entry<String, Object> entry : source.entrySet()) {
            List<Map<String, Object>> rows = new ArrayList<Map<String, Object>>();
            for (Object row : (List<Object>) entry.getValue()) {
                Map<String, Object> record = new LinkedHashMap<String, Object>((Map<String, Object>) row);
                record.put("id", Json.text(record.get("id")));
                rows.add(record);
            }
            documents.put(entry.getKey(), rows);
        }
    }

    private void persist() {
        Map<String, Object> state = new LinkedHashMap<String, Object>();
        state.put("documents", documents);
        state.put("users", users);
        try {
            Path temp = Paths.get(file.toString() + ".tmp");
            Files.write(temp, Json.write(state).getBytes(StandardCharsets.UTF_8));
            Files.move(temp, file, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException error) {
            throw new RuntimeException(error);
        }
    }

    synchronized List<Map<String, Object>> list(String resource) {
        return new ArrayList<Map<String, Object>>(documents.get(resource));
    }

    synchronized Map<String, Object> get(String resource, String id) {
        for (Map<String, Object> record : documents.get(resource)) {
            if (id.equals(record.get("id"))) return record;
        }
        return null;
    }

    synchronized Map<String, Object> save(String resource, Map<String, Object> record) {
        List<Map<String, Object>> rows = documents.get(resource);
        for (int i = 0; i < rows.size(); i++) {
            if (record.get("id").equals(rows.get(i).get("id"))) {
                rows.set(i, record);
                persist();
                return record;
            }
        }
        rows.add(record);
        persist();
        return record;
    }

    synchronized boolean remove(String resource, String id) {
        List<Map<String, Object>> rows = documents.get(resource);
        for (int i = 0; i < rows.size(); i++) {
            if (id.equals(rows.get(i).get("id"))) {
                rows.remove(i);
                persist();
                return true;
            }
        }
        return false;
    }

    synchronized Map<String, Object> user(String email) {
        return users.get(email);
    }

    synchronized void saveUser(Map<String, Object> user) {
        users.put((String) user.get("email"), user);
        persist();
    }
}
'''

JAVA_AUTH = '''import javax.crypto.Mac;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;

/** Sign-in: accounts with hashed passwords (PBKDF2) and signed tokens (HMAC-SHA256). */
public class Auth {
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final int TOKEN_DAYS = 30;
    private final Store store;
    private final byte[] secret;
    /** Set REQUIRE_AUTH=1 so only signed-in users can change data. */
    private final boolean requireAuth = "1".equals(System.getenv("REQUIRE_AUTH"));

    Auth(Store store) {
        this.store = store;
        String env = System.getenv("SECRET_KEY");
        this.secret = (env == null ? "change-me-in-production" : env).getBytes(StandardCharsets.UTF_8);
    }

    static String randomHex(int bytes) {
        byte[] value = new byte[bytes];
        RANDOM.nextBytes(value);
        return hex(value);
    }

    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format("%02x", b));
        return out.toString();
    }

    private static String hash(String password, String salt) {
        try {
            PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt.getBytes(StandardCharsets.UTF_8), 100000, 256);
            return salt + "$" + hex(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded());
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private String sign(String payload) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            return hex(mac.doFinal(payload.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }

    private String token(String email) {
        long expires = System.currentTimeMillis() / 1000 + TOKEN_DAYS * 86400L;
        String payload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString((email + "|" + expires).getBytes(StandardCharsets.UTF_8));
        return payload + "." + sign(payload);
    }

    /** The signed-in user for an "Authorization: Bearer ..." header, or null. */
    Map<String, Object> currentUser(String header) {
        if (header == null || !header.toLowerCase().startsWith("bearer ") || header.indexOf('.') == -1) return null;
        String value = header.substring(7).trim();
        int dot = value.lastIndexOf('.');
        String payload = value.substring(0, dot);
        byte[] given = value.substring(dot + 1).getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(given, sign(payload).getBytes(StandardCharsets.UTF_8))) return null;
        String decoded;
        try {
            decoded = new String(Base64.getUrlDecoder().decode(payload), StandardCharsets.UTF_8);
        } catch (IllegalArgumentException error) {
            return null;
        }
        int bar = decoded.lastIndexOf('|');
        if (bar == -1 || Long.parseLong(decoded.substring(bar + 1)) < System.currentTimeMillis() / 1000) return null;
        Map<String, Object> user = store.user(decoded.substring(0, bar));
        return user == null ? null : publicUser(user);
    }

    void requireWriter(String header) {
        if (requireAuth && currentUser(header) == null) throw new Main.ApiError(401, "Sign in to change data.");
    }

    /** Sign in. The first sign-in with a new email creates the account. */
    Map<String, Object> login(Map<String, Object> body) {
        String email = body.get("email") == null ? "" : Json.text(body.get("email")).trim().toLowerCase();
        String password = body.get("password") == null ? "" : Json.text(body.get("password"));
        if (email.indexOf('@') == -1) throw new Main.ApiError(422, "Enter a valid email address.");
        Map<String, Object> user = store.user(email);
        if (user == null) {
            user = new LinkedHashMap<String, Object>();
            user.put("email", email);
            user.put("name", body.get("name") == null ? email.substring(0, email.indexOf('@')) : Json.text(body.get("name")));
            user.put("password", password.isEmpty() ? null : hash(password, randomHex(8)));
            store.saveUser(user);
        } else if (user.get("password") != null) {
            String stored = (String) user.get("password");
            if (!MessageDigest.isEqual(stored.getBytes(StandardCharsets.UTF_8),
                    hash(password, stored.substring(0, stored.indexOf('$'))).getBytes(StandardCharsets.UTF_8))) {
                throw new Main.ApiError(401, "Wrong email or password.");
            }
        }
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("token", token(email));
        result.put("user", publicUser(user));
        return result;
    }

    private static Map<String, Object> publicUser(Map<String, Object> user) {
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("email", user.get("email"));
        out.put("name", user.get("name"));
        return out;
    }
}
'''

JAVA_DOCKER = '''FROM eclipse-temurin:21-jdk AS build
WORKDIR /app
COPY src ./src
RUN mkdir out && javac -d out src/*.java

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /app/out ./out
COPY schema.json seed.json ./
EXPOSE 8000
CMD ["java", "-cp", "out", "Main"]
'''


# ---------------------------------------------------------------- assembling

def backend_files(plan: dict, language: str) -> dict[str, str]:
    """The backend/ folder for a plan."""
    app = plan["app_name"].replace('"', "'")
    slug = "".join(c if c.isalnum() else "-" for c in app.lower()).strip("-") or "app"
    shared = {
        "backend/schema.json": json.dumps(schema_for(plan["data"]), indent=2) + "\n",
        "backend/seed.json": json.dumps(plan["data"], ensure_ascii=False, indent=2) + "\n",
        "backend/.env.example": "PORT=8000\nSECRET_KEY=change-me-in-production\n# Set to 1 so only signed-in users can change data\n"
                                "REQUIRE_AUTH=0\n# Comma-separated list of allowed frontends, or * for any\nCORS_ORIGINS=*\n",
    }
    if language == "python":
        files = {
            "backend/main.py": PY_MAIN,
            "backend/app/__init__.py": "",
            "backend/app/store.py": PY_STORE,
            "backend/app/auth.py": PY_AUTH,
            "backend/app/resources.py": PY_RESOURCES,
            "backend/requirements.txt": "fastapi>=0.110\nuvicorn[standard]>=0.29\n",
            "backend/Dockerfile": PY_DOCKER,
            "backend/.gitignore": "__pycache__/\n.venv/\ndata.sqlite3\n.env\n",
        }
    elif language == "node":
        package = {
            "name": f"{slug}-api", "private": True, "version": "1.0.0", "type": "module",
            "scripts": {"start": "node src/server.js", "dev": "node --watch src/server.js"},
            "dependencies": {"cors": "^2.8.5", "express": "^4.21.1"},
            "engines": {"node": ">=18"},
        }
        files = {
            "backend/package.json": json.dumps(package, indent=2) + "\n",
            "backend/src/server.js": NODE_SERVER,
            "backend/src/store.js": NODE_STORE,
            "backend/src/auth.js": NODE_AUTH,
            "backend/src/resources.js": NODE_RESOURCES,
            "backend/Dockerfile": NODE_DOCKER,
            "backend/.gitignore": "node_modules/\ndata.json\n.env\n",
        }
    elif language == "java":
        files = {
            "backend/src/Main.java": JAVA_MAIN,
            "backend/src/Json.java": JAVA_JSON,
            "backend/src/Store.java": JAVA_STORE,
            "backend/src/Auth.java": JAVA_AUTH,
            "backend/Dockerfile": JAVA_DOCKER,
            "backend/.gitignore": "out/\ndata.json\n.env\n",
        }
    else:
        raise ValueError(f"unknown backend language: {language}")
    return {path: content.replace("__APP__", app) for path, content in {**shared, **files}.items()}


def root_readme(plan: dict, language: str) -> str:
    info = LANGUAGES[language]
    rows = "\n".join(
        f"| `{name}` | {', '.join(f'`{field}` ({kind})' for field, kind in spec['fields'].items()) or '(free-form)'} |"
        for name, spec in schema_for(plan["data"])["resources"].items()
    )
    return f"""# {plan['app_name']}

{plan['tagline']}

A full-stack project:

- `frontend/`: React 18 + Vite + Tailwind CSS (React Router{', three.js' if 'three' in plan['packages'] else ''}{', Framer Motion' if 'framer-motion' in plan['packages'] else ''})
- `backend/`: {info['label']} REST API with sign-in, validation and seed data

## Run it

**1. Backend** (http://localhost:8000)

```bash
cd backend
{info['run']}
```

**2. Frontend** (in a second terminal, http://localhost:5173)

```bash
cd frontend
npm install
npm run dev
```

The frontend reads the API address from `VITE_API_URL` (see `frontend/.env.example`; the default is
`http://localhost:8000`). If the API can't be reached, the frontend keeps working on its sample data
in the browser, so you can still explore it.

With Docker instead: `docker compose up --build` starts the backend on port 8000.

## API

| Method | Path | What it does |
| --- | --- | --- |
| GET | `/api/health` | Check the server is up |
| GET | `/api/<resource>` | List records |
| GET | `/api/<resource>/<id>` | One record |
| POST | `/api/<resource>` | Create a record (JSON body, validated) |
| PUT / PATCH | `/api/<resource>/<id>` | Update a record |
| DELETE | `/api/<resource>/<id>` | Delete a record |
| POST | `/api/auth/login` | Sign in with `email` (and optional `password`, `name`); creates the account on first use. Returns a `token` |
| GET | `/api/auth/me` | The signed-in user (send `Authorization: Bearer <token>`) |

Errors are JSON: `{{"detail": "what went wrong"}}`.

### Resources

| Resource | Fields |
| --- | --- |
{rows}

The resources and field types live in `backend/schema.json`, and the starting records in
`backend/seed.json`. Edit those two files to change the data model; no code changes are needed.

## Settings (backend)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PORT` | `8000` | Port to listen on |
| `SECRET_KEY` | `change-me-in-production` | Signs the sign-in tokens. Change it before going live |
| `REQUIRE_AUTH` | `0` | Set to `1` so only signed-in users can create, change or delete records |
| `CORS_ORIGINS` | `*` | Which frontends may call the API |

## Where things are (frontend)

- `src/pages/`: one component per page; routes are in `src/App.jsx`
- `src/components/`: the shared layout and reusable sections
- `src/lib/store.js`: loads and saves data through the API (`useCollection`, `useStore`, `useAuth`)
"""


COMPOSE = """services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      SECRET_KEY: change-me-in-production
      REQUIRE_AUTH: "0"
"""


# The frontend's data layer for full-stack projects: same hooks as the browser-only store, but
# records are loaded from and saved to the API. In the NaturalSketch preview, or when the API can't
# be reached, it works on the sample data in the browser instead.
STORE_API_JS = """// Data layer: loads and saves records through the backend API (see ../../../backend).
// If the API can't be reached, the app keeps working on its sample data in the browser.
import { useSyncExternalStore } from "react";
import { API_URL } from "./config.js";
import seed from "./data.json";

const PREFIX = "ns-app:";
const listeners = new Set();
const cache = new Map();
const loaded = new Set();
let online = !(typeof window !== "undefined" && window.__NS_PREVIEW__);

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

// One request to the API. Throws an Error with the server's message if it fails.
export async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = read("store:auth:token", null);
  if (token) headers.Authorization = "Bearer " + token;
  const response = await fetch(API_URL + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Request failed (" + response.status + ")");
  return data;
}

function goOffline(error) {
  if (online) console.warn("[api] " + API_URL + " can't be reached (" + error.message + "). Using sample data in the browser.");
  online = false;
}

// Fetch a collection from the API the first time it is used.
function load(name) {
  if (!online || loaded.has(name)) return;
  loaded.add(name);
  request("GET", "/api/" + name).then((rows) => write("db:" + name, rows)).catch(goOffline);
}

// Send a change to the API in the background; the screen has already been updated.
function sync(method, path, body) {
  if (online) request(method, path, body).catch((error) => console.warn("[api] " + method + " " + path + ": " + error.message));
}

const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

export const db = {
  all(name) {
    load(name);
    return read("db:" + name, seed[name] || []);
  },
  get: (name, id) => db.all(name).find((r) => String(r.id) === String(id)) || null,
  add(name, record) {
    const saved = { ...record, id: record.id != null ? String(record.id) : newId() };
    write("db:" + name, [...db.all(name), saved]);
    sync("POST", "/api/" + name, saved);
    return saved;
  },
  update(name, id, patch) {
    write("db:" + name, db.all(name).map((r) => (String(r.id) === String(id) ? { ...r, ...patch } : r)));
    sync("PATCH", "/api/" + name + "/" + id, patch);
  },
  remove(name, id) {
    write("db:" + name, db.all(name).filter((r) => String(r.id) !== String(id)));
    sync("DELETE", "/api/" + name + "/" + id);
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

// Shared state kept in the browser (cart, favourites, settings, drafts...).
export function useStore(key, fallback) {
  const value = useSyncExternalStore(subscribe, () => read("store:" + key, fallback));
  const set = (next) => write("store:" + key, typeof next === "function" ? next(read("store:" + key, fallback)) : next);
  return [value, set];
}

export function useAuth() {
  const [user, setUser] = useStore("auth:user", null);
  return {
    user,
    // Signs in through the API (creating the account on first use). Throws if the password is wrong.
    async login(details) {
      if (online) {
        try {
          const result = await request("POST", "/api/auth/login", details);
          write("store:auth:token", result.token);
          setUser(result.user);
          return result.user;
        } catch (error) {
          if (!(error instanceof TypeError)) throw error; // a real answer from the server
          goOffline(error);
        }
      }
      const local = { name: details.name || (details.email || "").split("@")[0], email: details.email };
      setUser(local);
      return local;
    },
    logout() {
      write("store:auth:token", null);
      setUser(null);
    },
  };
}
"""

CONFIG_JS = """// Where the backend API lives. Set VITE_API_URL in a .env file to change it.
export const API_URL = ((import.meta.env && import.meta.env.VITE_API_URL) || "http://localhost:8000").replace(/\\/$/, "");
"""
