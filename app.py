"""Orange Lab – Inventory & Purchasing (Streamlit).

Run:  streamlit run app.py
"""
from __future__ import annotations

import os

import streamlit as st

from core import config
from ui import common, nav
from ui import (page_analysis, page_catalog, page_dashboard, page_inventory, page_orders, page_purchases,
                page_reports, page_settings, page_shortages, page_suppliers)

st.set_page_config(page_title=config.APP_SHORT, page_icon="🧪", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(common.CSS, unsafe_allow_html=True)

PAGES = {
    nav.DASHBOARD: ("لوحة المتابعة", page_dashboard.render),
    nav.INVENTORY: ("الجرد", page_inventory.render),
    nav.SHORTAGES: ("النواقص", page_shortages.render),
    nav.ORDERS: ("طلبات الشراء", page_orders.render),
    nav.PURCHASES: ("المشتريات والدفع", page_purchases.render),
    nav.SUPPLIERS: ("الشركات", page_suppliers.render),
    nav.CATALOG: ("الأصناف", page_catalog.render),
    nav.ANALYSIS: ("الاستهلاك وتقليل التكلفة", page_analysis.render),
    nav.REPORTS: ("التقارير", page_reports.render),
    nav.SETTINGS: ("الإعدادات", page_settings.render),
}


def login():
    from core import auth
    info = common.secrets_info()
    setup = auth.password_setup(info, common.load_db()["settings"])
    c1, c2, c3 = st.columns([1, 1.3, 1])
    with c2:
        if os.path.exists(config.LOGO_PATH):
            st.image(config.LOGO_PATH, width=220)
        st.markdown("### Inventory & Purchasing")
        st.caption("نظام متابعة المخزون والمشتريات – Orange Lab")
        with st.form("login"):
            name = st.text_input("Your name", placeholder="e.g. Dr Shereen")
            branch = st.selectbox("Branch", config.BRANCHES, format_func=lambda b: config.BRANCH_LABEL[b])
            pw = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
        if ok:
            role = auth.check_login(pw, setup)
            if not role:
                st.error("Wrong password.")
            else:
                st.session_state["user"] = {"name": name.strip() or "user", "branch": branch, "role": role}
                st.rerun()
        st.caption("🔐 Passwords: " + auth.describe(setup) +
                   (f" · file: `{info['source']}`" if info["source"] and info["source"] != "Streamlit secrets" else ""))
        for p in info["problems"]:
            st.warning(p)
        if "default" in (setup["staff"]["source"], setup["admin"]["source"]) and not info["problems"]:
            st.caption("No password found in secrets → defaults are active. In `.streamlit/secrets.toml` write:\n"
                       "```\n[auth]\npassword = \"...\"\nadmin_password = \"...\"\n```\n"
                       "or change them inside the app: Settings → 🔐 Passwords.")


def sidebar() -> str:
    u = common.user()
    with st.sidebar:
        if os.path.exists(config.LOGO_PATH):
            st.image(config.LOGO_PATH, width=150)
        st.markdown(f"**{u['name']}** · {config.BRANCH_LABEL.get(u['branch'], u['branch'])}"
                    f"{' · admin' if u['role'] == 'admin' else ''}")
        target = st.session_state.pop("nav_target", None)
        if target in PAGES:
            st.session_state["nav"] = target
        page = st.radio("Menu", list(PAGES.keys()), key="nav",
                        format_func=lambda k: f"{k}  ·  {PAGES[k][0]}", label_visibility="collapsed")
        st.divider()
        store = common.get_store()
        st.caption(("☁️ " if store.backend.name == "github" else "💾 ") + store.backend.describe())
        if st.session_state.get("load_error"):
            st.error(f"Could not read the data: {st.session_state['load_error']}")
        loaded = st.session_state.get("db_loaded_at", "")
        saved = st.session_state.get("last_saved", "")
        st.caption(f"Loaded {loaded}" + (f" · last save {saved}" if saved else ""))
        c1, c2 = st.columns(2)
        if c1.button("🔄 Refresh", use_container_width=True, help="Read the latest data (other branch / devices)"):
            common.load_db(force=True)
            st.rerun()
        if c2.button("🚪 Logout", use_container_width=True):
            for k in list(st.session_state.keys()):
                if k not in ("store",):
                    del st.session_state[k]
            st.rerun()
    return page


def main():
    if "user" not in st.session_state:
        login()
        return
    common.load_db()
    page = sidebar()
    PAGES[page][1]()


main()
