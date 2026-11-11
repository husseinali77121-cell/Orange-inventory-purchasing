"""Storage layer: one JSON document, saved locally and (optionally) on GitHub.

Every write is an *operation* (upsert / delete / settings / audit) that is applied
to the **latest** copy of the database just before saving. Two branches can
therefore write at the same time without overwriting each other's records
(record-level merge). New record ids are assigned at that moment too, so
sequential numbers (PO-2026-0007 …) never collide.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
import time
from datetime import datetime
from typing import Any

from . import config

COLLECTIONS = ["items", "suppliers", "counts", "purchase_orders", "invoices", "workload"]
ID_PREFIX = {
    "items": ("IT", 4, False),
    "suppliers": ("SUP", 3, False),
    "counts": ("CNT", 4, True),
    "purchase_orders": ("PO", 4, True),
    "invoices": ("INV", 4, True),
    "workload": ("WL", 4, False),
}
AUDIT_MAX = 3000


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def empty_db() -> dict:
    db = {"version": config.DB_VERSION, "settings": copy.deepcopy(config.DEFAULT_SETTINGS),
          "audit": []}
    for c in COLLECTIONS:
        db[c] = []
    return db


def starter_db() -> dict:
    """First start: the starter database shipped with the app, else an empty one."""
    if os.path.exists(config.STARTER_DB_PATH):
        with open(config.STARTER_DB_PATH, "r", encoding="utf-8") as f:
            return normalize_db(json.load(f))
    return empty_db()


def normalize_db(db: dict) -> dict:
    """Make sure every key exists (older files / partial imports)."""
    base = empty_db()
    for k, v in base.items():
        if k not in db or db[k] is None:
            db[k] = v
    # merge default settings that were added later
    for k, v in config.DEFAULT_SETTINGS.items():
        db["settings"].setdefault(k, copy.deepcopy(v))
    return db


# ------------------------------------------------------------------ id helpers
def next_id(db: dict, collection: str, year: int | None = None) -> str:
    prefix, width, yearly = ID_PREFIX[collection]
    existing = {r.get("id", "") for r in db.get(collection, [])}
    if yearly:
        year = year or datetime.now().year
        head = f"{prefix}-{year}-"
    else:
        head = f"{prefix}-"
    n = 0
    for rid in existing:
        if rid.startswith(head):
            tail = rid[len(head):]
            if tail.isdigit():
                n = max(n, int(tail))
    while True:
        n += 1
        cand = f"{head}{n:0{width}d}"
        if cand not in existing:
            return cand


# ------------------------------------------------------------------ operations
def op_upsert(collection: str, record: dict) -> dict:
    return {"op": "upsert", "collection": collection, "record": copy.deepcopy(record)}


def op_insert(collection: str, record: dict, year: int | None = None) -> dict:
    """Insert with an id assigned at save time (record['id'] ignored)."""
    return {"op": "insert", "collection": collection, "record": copy.deepcopy(record),
            "year": year}


def op_delete(collection: str, rid: str) -> dict:
    return {"op": "delete", "collection": collection, "id": rid}


def op_settings(patch: dict) -> dict:
    return {"op": "settings", "patch": copy.deepcopy(patch)}


def op_audit(user: str, action: str, ref: str = "") -> dict:
    return {"op": "audit", "entry": {"ts": now_iso(), "user": user, "action": action,
                                      "ref": ref}}


def op_replace_all(db: dict) -> dict:
    return {"op": "replace_all", "db": copy.deepcopy(db)}


def apply_ops(db: dict, ops: list[dict]) -> tuple[dict, list[str]]:
    """Apply operations in order. Returns (db, ids_of_inserted_or_upserted)."""
    db = normalize_db(db)
    ids: list[str] = []
    for op in ops:
        kind = op["op"]
        if kind == "replace_all":
            db = normalize_db(copy.deepcopy(op["db"]))
            continue
        if kind == "settings":
            db["settings"].update(op["patch"])
            continue
        if kind == "audit":
            db["audit"].append(op["entry"])
            if len(db["audit"]) > AUDIT_MAX:
                db["audit"] = db["audit"][-AUDIT_MAX:]
            continue
        coll = db[op["collection"]]
        if kind == "delete":
            db[op["collection"]] = [r for r in coll if r.get("id") != op["id"]]
            continue
        rec = copy.deepcopy(op["record"])
        rec["updated_at"] = now_iso()
        if kind == "insert":
            rec["id"] = next_id(db, op["collection"], op.get("year"))
            rec.setdefault("created_at", rec["updated_at"])
            coll.append(rec)
        elif kind == "upsert":
            if not rec.get("id"):
                rec["id"] = next_id(db, op["collection"])
            for i, r in enumerate(coll):
                if r.get("id") == rec["id"]:
                    rec.setdefault("created_at", r.get("created_at", rec["updated_at"]))
                    coll[i] = rec
                    break
            else:
                rec.setdefault("created_at", rec["updated_at"])
                coll.append(rec)
        ids.append(rec["id"])
    return db, ids


# ------------------------------------------------------------------ backends
class LocalBackend:
    name = "local"

    def __init__(self, path: str = config.LOCAL_DB_PATH):
        self.path = path

    def describe(self) -> str:
        return f"Local file: {os.path.relpath(self.path, config.BASE_DIR)}"

    def load(self) -> tuple[dict, Any]:
        if not os.path.exists(self.path):
            return starter_db(), None
        with open(self.path, "r", encoding="utf-8") as f:
            return normalize_db(json.load(f)), None

    def save(self, db: dict, token: Any = None) -> Any:
        write_json_atomic(self.path, db)
        return None


class GitHubBackend:
    """Stores the JSON file in a GitHub repository (contents API)."""
    name = "github"

    def __init__(self, token: str, repo: str, path: str, branch: str = "main",
                 local_mirror: str = config.LOCAL_DB_PATH):
        self.token, self.repo, self.path, self.branch = token, repo, path, branch
        self.local_mirror = local_mirror

    def describe(self) -> str:
        return f"GitHub: {self.repo}/{self.path} ({self.branch})"

    # -- http helpers
    def _req(self, method: str, url: str, payload: dict | None = None):
        import requests  # imported lazily so local mode needs nothing extra
        headers = {"Authorization": f"Bearer {self.token}",
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        r = requests.request(method, url, headers=headers, json=payload, timeout=30)
        return r

    def load(self) -> tuple[dict, Any]:
        url = f"https://api.github.com/repos/{self.repo}/contents/{self.path}?ref={self.branch}"
        r = self._req("GET", url)
        if r.status_code == 404:
            return starter_db(), None
        if r.status_code != 200:
            raise RuntimeError(f"GitHub read failed ({r.status_code}): {r.text[:200]}")
        meta = r.json()
        sha = meta["sha"]
        content = meta.get("content") or ""
        if meta.get("encoding") != "base64" or not content:
            # files > 1 MB: read the blob instead
            br = self._req("GET", f"https://api.github.com/repos/{self.repo}/git/blobs/{sha}")
            if br.status_code != 200:
                raise RuntimeError(f"GitHub blob read failed ({br.status_code})")
            content = br.json()["content"]
        db = json.loads(base64.b64decode(content).decode("utf-8"))
        db = normalize_db(db)
        try:
            write_json_atomic(self.local_mirror, db)
        except OSError:
            pass
        return db, sha

    def save(self, db: dict, token: Any = None) -> Any:
        url = f"https://api.github.com/repos/{self.repo}/contents/{self.path}"
        body = json.dumps(db, ensure_ascii=False, indent=1).encode("utf-8")
        payload = {"message": f"inventory update {now_iso()}",
                   "content": base64.b64encode(body).decode("ascii"),
                   "branch": self.branch}
        if token:
            payload["sha"] = token
        r = self._req("PUT", url, payload)
        if r.status_code in (409, 422):
            raise ConflictError(r.text[:200])
        if r.status_code not in (200, 201):
            raise RuntimeError(f"GitHub save failed ({r.status_code}): {r.text[:200]}")
        try:
            write_json_atomic(self.local_mirror, db)
        except OSError:
            pass
        return r.json()["content"]["sha"]


class ConflictError(RuntimeError):
    pass


def write_json_atomic(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Store:
    """Facade used by the app."""

    def __init__(self, backend):
        self.backend = backend

    def load(self) -> dict:
        db, _ = self.backend.load()
        return db

    def commit(self, ops: list[dict], retries: int = 4) -> tuple[dict, list[str]]:
        """Read latest → apply ops → save. Retries on concurrent-write conflicts."""
        last_err = None
        for attempt in range(retries):
            db, token = self.backend.load()
            new_db, ids = apply_ops(db, ops)
            try:
                self.backend.save(new_db, token)
                return new_db, ids
            except ConflictError as e:      # someone saved in between – try again
                last_err = e
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"Could not save after {retries} attempts: {last_err}")


def make_store(secrets: dict | None) -> Store:
    gh = (secrets or {}).get("github") if secrets else None
    if gh and gh.get("token") and gh.get("repo"):
        return Store(GitHubBackend(token=gh["token"], repo=gh["repo"],
                                   path=gh.get("path", "data/inventory_db.json"),
                                   branch=gh.get("branch", "main")))
    return Store(LocalBackend())
