"""One-time import of projects saved by the old file-based storage (data/projects/) into Supabase.

Run:  python migrate_local_data.py
Projects already in the database are skipped, so it is safe to run twice.
"""

import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import projects  # noqa: E402
import vision_processor  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

LOCAL = Path(__file__).parent / "data" / "projects"


def import_project(folder: Path) -> str:
    meta = json.loads((folder / "project.json").read_text(encoding="utf-8"))
    pid = meta["id"]
    try:
        projects.load(pid)
        return f"{meta['name']}: already imported, skipped"
    except KeyError:
        pass

    with projects.pool().connection() as conn, conn.transaction():
        conn.execute(
            """insert into naturalsketch.projects
               (id, name, prompt, features, brief, status, error, created_at, updated_at)
               values (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s))""",
            (pid, meta["name"], meta["prompt"], Jsonb(meta["features"]), Jsonb(meta["brief"]) if meta["brief"] else None,
             "ready" if meta["versions"] else "error", meta.get("error"), meta["created"], meta["updated"]),
        )
        for index, image in enumerate(meta["images"]):
            data = vision_processor.normalize_upload((folder / "uploads" / image["file"]).read_bytes())
            conn.execute(
                "insert into naturalsketch.uploads (project_id, idx, kind, mime, data) values (%s, %s, %s, 'image/jpeg', %s)",
                (pid, index, image["kind"], data),
            )
        for message in meta["chat"]:
            conn.execute(
                "insert into naturalsketch.messages (project_id, role, content, created_at) values (%s, %s, %s, to_timestamp(%s))",
                (pid, message["role"], message["content"], message["t"]),
            )
        for entry in meta["log"]:
            conn.execute(
                "insert into naturalsketch.logs (project_id, kind, msg, model, created_at) values (%s, %s, %s, %s, to_timestamp(%s))",
                (pid, entry["kind"], entry["msg"], entry.get("model"), entry["t"]),
            )

    # Versions are re-numbered in order; write_version also sets the current version.
    for version in meta["versions"]:
        root = folder / "versions" / str(version["n"])
        files = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}
        projects.write_version(pid, files, version["message"])
    return f"{meta['name']}: imported with {len(meta['versions'])} versions"


if __name__ == "__main__":
    projects.init()
    folders = [f for f in LOCAL.iterdir() if (f / "project.json").exists()] if LOCAL.exists() else []
    if not folders:
        print("No local projects found.")
    try:
        for folder in folders:
            print(import_project(folder))
    finally:
        projects.close()
    print("Done. Once you've checked the app, you can delete the data/ folder.")
