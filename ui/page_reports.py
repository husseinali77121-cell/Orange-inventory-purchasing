"""Reports: purchases register (Excel/PDF), statements, counts, expiry, spend analysis."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import config, excel_reports, logic, pdf_reports
from ui import common


def render():
    common.page_title("Reports", "التقارير", "Everything downloadable as Excel or PDF.")
    d = common.db()
    t = st.tabs(["🧾 Purchases register", "🏢 Supplier statement", "📋 Inventory counts", "⏳ Expiry",
                 "📈 Spend analysis"])
    sups, sup_by_id, sup_by_name = common.supplier_maps(d)
    with t[0]:
        st.caption("Like the annual 'Kits & consumables purchasing' sheet: company, what was supplied, kit / lot / "
                   "expiry, totals and when it was paid. Excel keeps live formulas.")
        c = st.columns([1.4, 1.4])
        with c[0]:
            start, end = common.period_picker("rep_reg", "This year")
        sname = c[1].selectbox("Supplier", ["All"] + [s["name"] for s in sups], key="rep_reg_sup")
        sid = sup_by_name[sname]["id"] if sname != "All" else None
        n = len(logic.invoices_in(d, start, end, sid))
        st.caption(f"{n} invoices in this period.")
        tag = f"{start or 'start'}_{end or 'today'}"
        c = st.columns(2)
        if st.toggle("Prepare files", key="rep_reg_go", help="Builds the Excel and PDF for the period"):
            with c[0]:
                common.download("⬇️ Excel register", excel_reports.register_xlsx(d, start, end, sid),
                                f"Purchases_{tag}.xlsx", "rep_reg_x", "xlsx")
            with c[1]:
                common.download("⬇️ PDF register", pdf_reports.register_pdf(d, start, end, sid),
                                f"Purchases_{tag}.pdf", "rep_reg_p")
    with t[1]:
        if sups:
            c = st.columns([1.4, 1.4])
            sname = c[0].selectbox("Supplier", [s["name"] for s in sups], key="rep_stm_sup")
            with c[1]:
                start, end = common.period_picker("rep_stm", "All time")
            sid = sup_by_name[sname]["id"]
            c = st.columns(2)
            with c[0]:
                common.download("⬇️ Statement PDF", pdf_reports.statement_pdf(d, sid, start, end),
                                f"Statement_{sname}.pdf", "rep_stm_p")
            with c[1]:
                common.download("⬇️ Statement Excel", excel_reports.statement_xlsx(d, sid, start, end),
                                f"Statement_{sname}.xlsx", "rep_stm_x", "xlsx")
    with t[2]:
        sessions = sorted(d.get("counts", []), key=lambda c: c.get("date", ""), reverse=True)
        if sessions:
            ref = st.selectbox("Count", [s["id"] for s in sessions], key="rep_cnt",
                               format_func=lambda i: next(f"{s['id']} · {logic.fmt_date(s['date'])} · {s['group']} · "
                                                          f"{config.BRANCH_LABEL.get(s['branch'], s['branch'])}"
                                                          for s in sessions if s["id"] == i))
            sess = next(s for s in sessions if s["id"] == ref)
            c = st.columns(2)
            with c[0]:
                common.download("⬇️ PDF (paper layout)", pdf_reports.count_pdf(sess, d), f"Inventory_{ref}.pdf",
                                "rep_cnt_p")
            with c[1]:
                common.download("⬇️ Excel", excel_reports.count_xlsx(sess, d), f"Inventory_{ref}.xlsx",
                                "rep_cnt_x", "xlsx")
        else:
            st.info("No counts yet.")
    with t[3]:
        days = st.slider("Within (days)", 0, 365, int(d["settings"].get("expiry_warn_days", 60)), 15, key="rep_exp_d")
        c = st.columns(2)
        with c[0]:
            common.download("⬇️ Expiry PDF", pdf_reports.expiry_pdf(d, days), f"Expiry_{date.today()}.pdf", "rep_exp_p")
        with c[1]:
            common.download("⬇️ Expiry Excel", excel_reports.expiry_xlsx(d, days), f"Expiry_{date.today()}.xlsx",
                            "rep_exp_x", "xlsx")
    with t[4]:
        start, end = common.period_picker("rep_sp", "This year")
        c = st.columns(2)
        with c[0]:
            st.markdown("**By supplier**")
            df = pd.DataFrame(logic.spend_by(d, "Supplier", start, end))
            if not df.empty:
                st.bar_chart(df.set_index("Supplier"), color="#E46C0A", height=260, horizontal=True) \
                    if _has_horizontal() else st.bar_chart(df.set_index("Supplier"), color="#E46C0A", height=260)
                st.dataframe(df, hide_index=True, use_container_width=True)
        with c[1]:
            st.markdown("**By category**")
            df = pd.DataFrame(logic.spend_by(d, "Category", start, end))
            if not df.empty:
                st.bar_chart(df.set_index("Category"), color="#E46C0A", height=260)
                st.dataframe(df, hide_index=True, use_container_width=True)
        st.markdown("**Top items**")
        df = pd.DataFrame(logic.spend_by(d, "Catalog item", start, end)).head(20)
        if not df.empty:
            st.dataframe(df, hide_index=True, use_container_width=True)


def _has_horizontal() -> bool:
    import inspect
    try:
        return "horizontal" in inspect.signature(st.bar_chart).parameters
    except (TypeError, ValueError):
        return False
