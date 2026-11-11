"""Secrets discovery and passwords.

Why this exists: Streamlit only reads `.streamlit/secrets.toml` from the folder you
*start* it from. If the app is started from another folder, if Windows saved the file
as `secrets.toml.txt`, if the file has a BOM or a TOML mistake, or if the passwords
are written without the `[auth]` header, Streamlit silently finds nothing and the app
fell back to the default passwords. Here every one of those cases is handled or shown.

Password priority:  changed inside the app (Settings → Passwords)  >  secrets file  >  defaults.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _pysecrets

from . import config

DEFAULT_STAFF = "orange"
DEFAULT_ADMIN = "orange-admin"


def _parse_toml(text: str) -> dict:
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ModuleNotFoundError:  # Python 3.10 – Streamlit ships the `toml` package
        import toml
        return toml.loads(text)


def candidate_files() -> list[str]:
    base = config.BASE_DIR
    home = os.path.expanduser("~")
    return [os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
            os.path.join(base, ".streamlit", "secrets.toml"),
            os.path.join(base, "secrets.toml"),
            os.path.join(home, ".streamlit", "secrets.toml")]


def load_secrets(st_secrets=None) -> dict:
    """Returns {'data': dict, 'source': str, 'problems': [str]}."""
    problems: list[str] = []
    # 1) what Streamlit itself loaded (Streamlit Cloud "Secrets" box, or a file it found)
    if st_secrets is not None:
        try:
            data = {k: (dict(v) if hasattr(v, "keys") else v) for k, v in st_secrets.items()}
            if data:
                return {"data": data, "source": "Streamlit secrets", "problems": problems}
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 – a TOML error in the file Streamlit found
            problems.append(f"Streamlit could not read the secrets file: {e}")
    # 2) look for the file ourselves (app folder, wrong start folder, BOM …)
    seen = set()
    for path in candidate_files():
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.exists(path + ".txt"):
            problems.append(f"Found '{os.path.basename(path)}.txt' – Windows added '.txt'. Rename it to "
                            f"'secrets.toml' (folder {os.path.dirname(path)}).")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                text = f.read().decode("utf-8-sig")
            data = _parse_toml(text)
            if data:
                return {"data": data, "source": path, "problems": problems}
            problems.append(f"{path} is empty.")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{path} has a mistake: {e}. Put text values in quotes, e.g. password = \"1234\".")
    example = os.path.join(config.BASE_DIR, ".streamlit", "secrets.toml.example")
    if not any(os.path.exists(p) for p in seen) and os.path.exists(example):
        problems.append("Only 'secrets.toml.example' exists – copy it to '.streamlit/secrets.toml' and edit the copy.")
    return {"data": {}, "source": "", "problems": problems}


def _s(v) -> str:
    return "" if v is None else str(v).strip()


# ------------------------------------------------------------------ hashing (in-app passwords)
def hash_password(pw: str, salt: str | None = None) -> dict:
    salt = salt or _pysecrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return {"salt": salt, "hash": h}


def check_hash(pw: str, rec: dict) -> bool:
    if not rec or not rec.get("salt") or not rec.get("hash"):
        return False
    return hmac.compare_digest(hash_password(pw, rec["salt"])["hash"], rec["hash"])


# ------------------------------------------------------------------ resolution
def password_setup(sec: dict, settings: dict) -> dict:
    """Which password is active for staff/admin and where it comes from."""
    data = sec.get("data", {})
    auth = data.get("auth") if isinstance(data.get("auth"), dict) else {}
    staff_sec = _s(auth.get("password") or auth.get("staff_password") or data.get("password")
                   or data.get("staff_password"))
    admin_sec = _s(auth.get("admin_password") or data.get("admin_password"))
    app = (settings or {}).get("auth") or {}
    out = {}
    for role, sec_pw, default in (("staff", staff_sec, DEFAULT_STAFF), ("admin", admin_sec, DEFAULT_ADMIN)):
        if app.get(role):
            out[role] = {"source": "app", "plain": None, "hash": app[role]}
        elif sec_pw:
            out[role] = {"source": "secrets", "plain": sec_pw, "hash": None}
        else:
            out[role] = {"source": "default", "plain": default, "hash": None}
    return out


def check_login(pw: str, setup: dict) -> str | None:
    """Returns 'admin', 'staff' or None."""
    pw = _s(pw)
    if not pw:
        return None
    for role in ("admin", "staff"):
        r = setup[role]
        if r["hash"] and check_hash(pw, r["hash"]):
            return role
        if r["plain"] is not None and hmac.compare_digest(pw, r["plain"]):
            return role
    return None


def describe(setup: dict) -> str:
    label = {"app": "changed in the app", "secrets": "secrets file", "default": "⚠️ DEFAULT"}
    return f"staff: {label[setup['staff']['source']]} · admin: {label[setup['admin']['source']]}"
