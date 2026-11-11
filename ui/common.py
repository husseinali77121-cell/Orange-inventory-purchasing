"""Shared Streamlit helpers: session db, saving, small widgets."""
from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
import streamlit as st

from core import config, logic, storage


# ------------------------------------------------------------------ secrets / store
def secrets() -> dict:
    try:
        return {k: dict(v) if hasattr(v, "keys") else v for k, v in st.secrets.items()}
    except Exception:  # noqa: BLE001 - no secrets file
        return {}


def get_store() -> storage.Store:
    if "store" not in st.session_state:
        st.session_state["store"] = storage.make_store(secrets())
    return st.session_state["store"]


def load_db(force: bool = False) -> dict:
    if force or "db" not in st.session_state:
        try:
            st.session_state["db"] = get_store().load()
            st.session_state["db_loaded_at"] = datetime.now().strftime("%H:%M:%S")
            st.session_state.pop("load_error", None)
        except Exception as e:  # noqa: BLE001
            st.session_state["load_error"] = str(e)
            if "db" not in st.session_state:
                st.session_state["db"] = storage.LocalBackend().load()[0]
    return st.session_state["db"]


def db() -> dict:
    return load_db()


def user() -> dict:
    return st.session_state.get("user", {"name": "?", "branch": "Diamond", "role": "staff"})


def is_admin() -> bool:
    return user().get("role") == "admin"


def commit(ops: list[dict], message: str, ref: str = "") -> list[str] | None:
    """Save operations. Shows a clear error if saving fails (nothing is half-saved)."""
    u = user()
    try:
        new_db, ids = get_store().commit(ops + [storage.op_audit(f"{u['name']} ({u['branch']})", message, ref)])
    except Exception as e:  # noqa: BLE001
        st.error(f"❌ NOT SAVED – {message}. Reason: {e}. Please try again; if it keeps failing, "
                 f"download a backup from Settings.")
        return None
    st.session_state["db"] = new_db
    st.session_state["last_saved"] = datetime.now().strftime("%H:%M:%S")
    st.toast(f"✅ Saved – {message}")
    return ids


def go(page: str, **state):
    """Jump to another page on the next run."""
    for k, v in state.items():
        st.session_state[k] = v
    st.session_state["nav_target"] = page
    st.rerun()


# ------------------------------------------------------------------ lookups
def item_maps(d: dict, active_only: bool = False):
    items = logic.sorted_items(d, active_only=active_only)
    by_id = {i["id"]: i for i in items}
    by_name = {i["name"]: i for i in items}
    return items, by_id, by_name


def supplier_maps(d: dict, active_only: bool = False):
    sups = sorted([s for s in d.get("suppliers", []) if not active_only or s.get("active", True)],
                  key=lambda s: s.get("name", "").lower())
    return sups, {s["id"]: s for s in sups}, {s["name"]: s for s in sups}


def branch_code() -> str:
    return config.BRANCH_CODE.get(user().get("branch"), "D")


# ------------------------------------------------------------------ dataframe helpers
def records(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict("records"):
        clean = {}
        for k, v in r.items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            elif v is pd.NaT:
                v = None
            elif hasattr(v, "item") and not isinstance(v, (str, bytes)):
                try:
                    v = v.item()
                except Exception:  # noqa: BLE001
                    pass
            clean[k] = v
        out.append(clean)
    return out


def money(v) -> str:
    cur = db()["settings"].get("currency", "EGP")
    return f"{logic.fmt_num(v) or '0'} {cur}"


def avail_icon(avail: str) -> str:
    return {config.AVAIL_AVAILABLE: "⚫ Available", config.AVAIL_EXPIRED: "🔴 Expired",
            config.AVAIL_SMALL: "🔵 Small amount", config.AVAIL_NONE: "🟢 Not available"}.get(avail, avail)


def status_icon(s: str) -> str:
    return {"G": "G ✅", "B": "B 🛒", "-": "– (n/a)"}.get(s, s)


def date_value(v) -> date:
    return logic.parse_date(v) or date.today()


def period_picker(key: str, default: str = "This year") -> tuple[date | None, date | None]:
    t = date.today()
    presets = ["This month", "Last month", "This year", "Last year", "All time", "Custom"]
    choice = st.selectbox("Period", presets, index=presets.index(default), key=f"{key}_preset")
    if choice == "This month":
        return t.replace(day=1), t
    if choice == "Last month":
        first = t.replace(day=1)
        last_end = first.fromordinal(first.toordinal() - 1)
        return last_end.replace(day=1), last_end
    if choice == "This year":
        return date(t.year, 1, 1), date(t.year, 12, 31)
    if choice == "Last year":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    if choice == "All time":
        return None, None
    c1, c2 = st.columns(2)
    s = c1.date_input("From", value=date(t.year, 1, 1), key=f"{key}_from", format="DD/MM/YYYY")
    e = c2.date_input("To", value=t, key=f"{key}_to", format="DD/MM/YYYY")
    return s, e


def download(label: str, data: bytes, filename: str, key: str, kind: str = "pdf"):
    mime = {"pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "json": "application/json"}[kind]
    st.download_button(label, data=data, file_name=filename, mime=mime, key=key, use_container_width=True)


CSS = """
<style>
:root { --orange: #E46C0A; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; }
h1, h2, h3 { color: #222; }
div[data-testid="stMetric"] { background: #FFF7F0; border: 1px solid #F6D3B5; border-radius: 12px;
  padding: 10px 14px; }
div[data-testid="stMetric"] label p { font-size: 0.85rem; color: #7a4a20; }
.oi-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.8rem;
  background:#FFF0E3; color:#9a4a07; border:1px solid #F6D3B5; margin-right:6px; }
.oi-muted { color:#777; font-size:0.85rem; }
.oi-title { display:flex; align-items:center; gap:12px; }
.oi-title small { color:#999; font-weight:400; }
section[data-testid="stSidebar"] { background: #FFF8F2; }
</style>
"""


def page_title(title: str, ar_title: str = "", sub: str = ""):
    st.markdown(f"<div class='oi-title'><h2 style='margin:0'>{title}</h2>"
                f"<small>{ar_title}</small></div>", unsafe_allow_html=True)
    if sub:
        st.markdown(f"<div class='oi-muted'>{sub}</div>", unsafe_allow_html=True)
    st.write("")
