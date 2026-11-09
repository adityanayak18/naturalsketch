"""Project storage in Postgres (Supabase).

Everything (projects, uploads, versions, generated files, chat and build logs)
lives in the `naturalsketch` schema. Supabase's public Data API only exposes the
`public` schema, so these tables are reachable only through this server.

Set DATABASE_URL in .env to the Supabase "Session pooler" connection string.
"""

import json
import os
import re
import secrets
import threading
from functools import lru_cache

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

ID_PATTERN = re.compile(r"^[a-f0-9]{10}$")
UPDATABLE = {"name", "prompt", "brief", "status", "error"}

SCHEMA_SQL = """
create schema if not exists naturalsketch;

create table if not exists naturalsketch.projects (
    id              text primary key,
    name            text not null default 'New app',
    prompt          text not null default '',
    features        jsonb not null default '[]',
    brief           jsonb,
    status          text not null default 'queued',
    error           text,
    current_version integer,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create table if not exists naturalsketch.uploads (
    project_id text not null references naturalsketch.projects (id) on delete cascade,
    idx        integer not null,
    kind       text not null,
    mime       text not null,
    data       bytea not null,
    primary key (project_id, idx)
);

create table if not exists naturalsketch.versions (
    project_id text not null references naturalsketch.projects (id) on delete cascade,
    n          integer not null,
    message    text not null,
    pages      jsonb not null,
    created_at timestamptz not null default now(),
    primary key (project_id, n)
);

create table if not exists naturalsketch.files (
    project_id text not null,
    version    integer not null,
    path       text not null,
    content    text not null,
    primary key (project_id, version, path),
    foreign key (project_id, version) references naturalsketch.versions (project_id, n) on delete cascade
);

create table if not exists naturalsketch.messages (
    id         bigserial primary key,
    project_id text not null references naturalsketch.projects (id) on delete cascade,
    role       text not null,
    content    text not null,
    created_at timestamptz not null default now()
);
create index if not exists messages_by_project on naturalsketch.messages (project_id, id);

create table if not exists naturalsketch.logs (
    id         bigserial primary key,
    project_id text not null references naturalsketch.projects (id) on delete cascade,
    kind       text not null,
    msg        text not null,
    model      text,
    created_at timestamptz not null default now()
);
create index if not exists logs_by_project on naturalsketch.logs (project_id, id);

-- Accounts: each project belongs to a Supabase Auth user (their id). Older projects have none
-- until the admin account claims them.
alter table naturalsketch.projects add column if not exists owner_id text;
-- 'site' (multi-page HTML prototype) or 'react' (a Vite + React project).
alter table naturalsketch.projects add column if not exists kind text not null default 'site';
create index if not exists projects_by_owner on naturalsketch.projects (owner_id, updated_at desc);

-- Builds and chat edits per user per day, so one account can't use up the shared AI quota.
create table if not exists naturalsketch.usage (
    owner_id text not null,
    day      date not null default current_date,
    actions  integer not null default 0,
    primary key (owner_id, day)
);
"""

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def _configure(conn: Connection) -> None:
    conn.autocommit = True
    # Supabase's poolers don't support server-side prepared statements.
    conn.prepare_threshold = None


def pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            url = os.environ.get("DATABASE_URL")
            if not url:
                raise RuntimeError("DATABASE_URL is not set. Add your Supabase connection string to .env.")
            kwargs = {"row_factory": dict_row, "connect_timeout": 15}
            # A separate password variable avoids URL-encoding passwords with @, #, / ...
            if os.environ.get("DATABASE_PASSWORD"):
                kwargs["password"] = os.environ["DATABASE_PASSWORD"]
            _pool = ConnectionPool(
                url,
                min_size=1,
                max_size=6,
                max_idle=300,
                configure=_configure,
                check=ConnectionPool.check_connection,  # the pooler drops idle connections
                kwargs=kwargs,
                open=False,
            )
            _pool.open(wait=True, timeout=30)
        return _pool


def _all(sql: str, params=()) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def _one(sql: str, params=()) -> dict | None:
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def _run(sql: str, params=()) -> None:
    with pool().connection() as conn:
        conn.execute(sql, params)


def init() -> None:
    """Connect and create the tables if they don't exist yet."""
    with pool().connection() as conn:
        conn.execute(SCHEMA_SQL)


def close() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


def valid_id(pid: str) -> bool:
    return bool(ID_PATTERN.match(pid or ""))


def _check(pid: str) -> None:
    if not valid_id(pid):
        raise KeyError(pid)


# ---------------------------------------------------------------- projects

def create(prompt: str, features: list[str], images: list[tuple[bytes, str, str]], owner: str | None = None,
           kind: str = "site") -> str:
    """images: (data, mime, kind) where kind is 'sketch' or 'screenshot'. Returns the new id."""
    pid = secrets.token_hex(5)
    with pool().connection() as conn, conn.transaction():
        conn.execute(
            "insert into naturalsketch.projects (id, prompt, features, owner_id, kind) values (%s, %s, %s, %s, %s)",
            (pid, prompt, Jsonb(features), owner, kind),
        )
        for index, (data, mime, kind) in enumerate(images):
            conn.execute(
                "insert into naturalsketch.uploads (project_id, idx, kind, mime, data) values (%s, %s, %s, %s, %s)",
                (pid, index, kind, mime, data),
            )
    return pid


def load(pid: str) -> dict:
    """The full project as the UI and the builder see it, in one round trip."""
    _check(pid)
    row = _one(
        """
        select p.id, p.name, p.prompt, p.features, p.brief, p.status, p.error, p.kind,
               p.current_version as current,
               extract(epoch from p.created_at)::float8 as created,
               extract(epoch from p.updated_at)::float8 as updated,
               coalesce((select json_agg(json_build_object('file', u.idx::text, 'kind', u.kind) order by u.idx)
                         from naturalsketch.uploads u where u.project_id = p.id), '[]') as images,
               coalesce((select json_agg(json_build_object('n', v.n, 'message', v.message, 'pages', v.pages,
                                                           'created', extract(epoch from v.created_at)) order by v.n)
                         from naturalsketch.versions v where v.project_id = p.id), '[]') as versions,
               coalesce((select json_agg(json_build_object('role', m.role, 'content', m.content,
                                                           't', extract(epoch from m.created_at)) order by m.id)
                         from naturalsketch.messages m where m.project_id = p.id), '[]') as chat,
               coalesce((select json_agg(l order by l.id) from (
                            select id, kind, msg, model, extract(epoch from created_at) as t
                            from naturalsketch.logs where project_id = p.id order by id desc limit 500) l), '[]') as log
        from naturalsketch.projects p
        where p.id = %s
        """,
        (pid,),
    )
    if row is None:
        raise KeyError(pid)
    return row


def list_all(owner: str | None = None) -> list[dict]:
    """The owner's projects, newest first. owner=None (local single-user mode) lists everything."""
    return _all(
        """
        select p.id, p.name, p.status, p.kind, p.current_version as current, left(p.prompt, 160) as prompt,
               extract(epoch from p.updated_at)::float8 as updated,
               (select count(*) from naturalsketch.versions v where v.project_id = p.id)::int as versions,
               (select min(u.idx)::text from naturalsketch.uploads u where u.project_id = p.id) as thumbnail
        from naturalsketch.projects p
        where %(owner)s::text is null or p.owner_id = %(owner)s
        order by p.updated_at desc
        limit 200
        """,
        {"owner": owner},
    )


def owner_of(pid: str) -> str | None:
    """Raises KeyError if the project doesn't exist."""
    _check(pid)
    row = _one("select owner_id from naturalsketch.projects where id = %s", (pid,))
    if row is None:
        raise KeyError(pid)
    return row["owner_id"]


def claim_unowned(owner: str) -> int:
    """Give every project without an owner to this account (used once for the admin)."""
    with pool().connection() as conn:
        return conn.execute("update naturalsketch.projects set owner_id = %s where owner_id is null", (owner,)).rowcount


def take_action(owner: str, daily_limit: int) -> bool:
    """Count one build or chat edit for today. False if the owner is already at the limit."""
    row = _one(
        """
        insert into naturalsketch.usage as u (owner_id, day, actions) values (%(o)s, current_date, 1)
        on conflict (owner_id, day) do update set actions = u.actions + 1
        where u.actions < %(limit)s
        returning actions
        """,
        {"o": owner, "limit": daily_limit},
    )
    return row is not None


def actions_today(owner: str) -> int:
    row = _one("select actions from naturalsketch.usage where owner_id = %s and day = current_date", (owner,))
    return row["actions"] if row else 0


def update(pid: str, **fields) -> None:
    _check(pid)
    unknown = set(fields) - UPDATABLE
    if unknown:
        raise ValueError(f"cannot update {unknown}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = %s" for name in fields)
    values = [Jsonb(v) if name == "brief" and v is not None else v for name, v in fields.items()]
    _run(f"update naturalsketch.projects set {assignments}, updated_at = now() where id = %s", (*values, pid))


def delete(pid: str) -> None:
    _check(pid)
    _run("delete from naturalsketch.projects where id = %s", (pid,))
    read_file.cache_clear()
    read_upload.cache_clear()


def add_log(pid: str, kind: str, msg: str, model: str | None = None) -> None:
    _run("insert into naturalsketch.logs (project_id, kind, msg, model) values (%s, %s, %s, %s)", (pid, kind, msg, model))


def add_message(pid: str, role: str, content: str) -> None:
    _run("insert into naturalsketch.messages (project_id, role, content) values (%s, %s, %s)", (pid, role, content))


def mark_interrupted() -> None:
    """Builds don't survive a server restart; flag them so the user can retry."""
    _run(
        """
        with stuck as (
            update naturalsketch.projects
            set status = case when current_version is null then 'error' else 'ready' end,
                error = case when current_version is null then 'The server restarted during the build.' end,
                updated_at = now()
            where status in ('queued', 'building')
            returning id
        )
        insert into naturalsketch.logs (project_id, kind, msg)
        select id, 'error', 'Interrupted by a server restart.' from stuck
        """
    )


# ---------------------------------------------------------------- uploads

@lru_cache(maxsize=64)
def read_upload(pid: str, index: int) -> tuple[bytes, str] | None:
    """Uploads never change, so they are cached in memory."""
    _check(pid)
    row = _one("select data, mime from naturalsketch.uploads where project_id = %s and idx = %s", (pid, index))
    return (bytes(row["data"]), row["mime"]) if row else None


# ---------------------------------------------------------------- versions and files

def write_version(pid: str, files: dict[str, str], message: str, pages: list[str] | None = None) -> int:
    """Store a full site snapshot as the next version and make it current.

    `pages` are the page tabs the studio shows; HTML sites derive them from their .html files.
    """
    _check(pid)
    if pages is None:
        plan = json.loads(files["_src/plan.json"]) if "_src/plan.json" in files else {}
        if plan.get("kind") == "react":  # page tabs are the routes you can open without an id
            pages = [r["slug"] for r in plan.get("routes", []) if not r.get("param")] or ["index"]
        else:
            pages = sorted(name[:-5] for name in files if name.endswith(".html") and "/" not in name)
            order = [page["slug"] for page in plan.get("pages", [])]  # keep the app's own order (home first)
            pages = [slug for slug in order if slug in pages] + [slug for slug in pages if slug not in order]

    with pool().connection() as conn, conn.transaction():
        conn.execute("select 1 from naturalsketch.projects where id = %s for update", (pid,))  # serialise numbering
        number = conn.execute(
            "select coalesce(max(n), 0) + 1 as n from naturalsketch.versions where project_id = %s", (pid,)
        ).fetchone()["n"]
        conn.execute(
            "insert into naturalsketch.versions (project_id, n, message, pages) values (%s, %s, %s, %s)",
            (pid, number, message, Jsonb(pages)),
        )
        with conn.cursor().copy("copy naturalsketch.files (project_id, version, path, content) from stdin") as copy:
            for path, content in files.items():
                copy.write_row((pid, number, path, content))
        conn.execute(
            "update naturalsketch.projects set current_version = %s, updated_at = now() where id = %s", (number, pid)
        )
    return number


def read_version(pid: str, version: int) -> dict[str, str]:
    _check(pid)
    rows = _all("select path, content from naturalsketch.files where project_id = %s and version = %s", (pid, version))
    return {row["path"]: row["content"] for row in rows}


@lru_cache(maxsize=1024)
def read_file(pid: str, version: int, path: str) -> str | None:
    """Versions are immutable, so served files are cached in memory."""
    _check(pid)
    row = _one(
        "select content from naturalsketch.files where project_id = %s and version = %s and path = %s",
        (pid, version, path),
    )
    return row["content"] if row else None
