"""Suppliers (companies): contact details, balances and statements."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import excel_reports, logic, pdf_reports, storage
from ui import common

COLS = {"name": "Name", "phone": "Phone", "whatsapp": "WhatsApp", "email": "Email", "contact": "Contact person",
        "address": "Address", "supplies": "Supplies", "payment_terms": "Payment terms", "notes": "Notes",
        "aliases": "Other spellings", "active": "Active"}


def render():
    common.page_title("Suppliers", "الشركات والموردين",
                      "Company name and phone appear on every purchase order and open WhatsApp directly.")
    d = common.db()
    sups = sorted(d.get("suppliers", []), key=lambda s: s.get("name", "").lower())
    base = pd.DataFrame([{**{v: s.get(k, "" if k != "active" else True) for k, v in COLS.items()}, "id": s["id"]}
                         for s in sups], columns=list(COLS.values()) + ["id"])
    for c in COLS.values():
        if c != "Active":
            base[c] = base[c].astype("object")
    base["Active"] = base["Active"].fillna(True).astype(bool)
    st.caption("Edit directly in the table, add a row at the bottom, then **Save**. "
               "'Other spellings' (comma separated) help the importer match names like 'ortho trade'.")
    ed = st.data_editor(base, key=f"sup_ed_{len(sups)}_{d['audit'][-1]['ts'] if d['audit'] else ''}",
                        num_rows="dynamic", hide_index=True, use_container_width=True,
                        column_order=list(COLS.values()), column_config={
                            "Name": st.column_config.TextColumn(required=True, width="medium"),
                            "Phone": st.column_config.TextColumn(width="small", help="e.g. 01012345678"),
                            "WhatsApp": st.column_config.TextColumn(width="small", help="If different from phone"),
                            "Active": st.column_config.CheckboxColumn(width="small")})
    if st.button("💾 Save suppliers", type="primary"):
        save(d, sups, ed)

    st.divider()
    st.markdown("#### Balances")
    bal = logic.supplier_balances(d)
    if bal:
        st.dataframe(pd.DataFrame(bal).drop(columns=["supplier_id"]), hide_index=True, use_container_width=True,
                     column_config={k: st.column_config.NumberColumn(format="%.2f")
                                    for k in ("Purchases", "Paid", "Balance")})
    st.markdown("#### Statement (كشف حساب)")
    if not sups:
        return
    c = st.columns([1.4, 1.6, 1, 1])
    names = [s["name"] for s in sups]
    sname = c[0].selectbox("Supplier", names, key="stm_sup")
    with c[1]:
        start, end = common.period_picker("stm", "This year")
    sup = next(s for s in sups if s["name"] == sname)
    with c[2]:
        common.download("⬇️ PDF", pdf_reports.statement_pdf(d, sup["id"], start, end),
                        f"Statement_{sname}_{date.today()}.pdf", "stm_pdf")
    with c[3]:
        common.download("⬇️ Excel", excel_reports.statement_xlsx(d, sup["id"], start, end),
                        f"Statement_{sname}_{date.today()}.xlsx", "stm_xlsx", "xlsx")
    rows, opening = logic.supplier_statement(d, sup["id"], start, end)
    st.caption(f"Opening balance: {common.money(opening)}")
    if rows:
        st.dataframe(pd.DataFrame([{"Date": logic.fmt_date(r["date"]), "Type": r["type"], "Ref": r["ref"],
                                    "Description": r["desc"], "Debit": r["debit"] or None,
                                    "Credit": r["credit"] or None, "Balance": r["balance"]} for r in rows]),
                     hide_index=True, use_container_width=True,
                     column_config={k: st.column_config.NumberColumn(format="%.2f")
                                    for k in ("Debit", "Credit", "Balance")})
    phone = sup.get("whatsapp") or sup.get("phone")
    if phone:
        st.link_button(f"💬 WhatsApp {sname}", logic.wa_link(phone, ""))


def save(d, sups, ed):
    rows = common.records(ed)
    names = [logic.clean_str(r.get("Name")) for r in rows if logic.clean_str(r.get("Name"))]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        st.error(f"Duplicate names: {', '.join(dup)}")
        return
    old = {s["id"]: s for s in sups}
    ops, kept = [], set()
    for r in rows:
        name = logic.clean_str(r.get("Name"))
        if not name:
            continue
        rec = {k: (bool(r.get(v)) if k == "active" else logic.clean_str(r.get(v))) for k, v in COLS.items()}
        sid = logic.clean_str(r.get("id"))
        if sid and sid in old:
            kept.add(sid)
            merged = {**old[sid], **rec}
            if merged != old[sid]:
                ops.append(storage.op_upsert("suppliers", merged))
        else:
            ops.append(storage.op_insert("suppliers", rec))
    used = {p.get("supplier_id") for p in d.get("purchase_orders", [])} | \
           {i.get("supplier_id") for i in d.get("invoices", [])}
    for sid, s in old.items():
        if sid in kept:
            continue
        if sid in used:
            ops.append(storage.op_upsert("suppliers", {**s, "active": False}))
            st.info(f"{s['name']} has orders/invoices – set to inactive instead of deleting.")
        else:
            ops.append(storage.op_delete("suppliers", sid))
    if not ops:
        st.info("No changes.")
        return
    if common.commit(ops, f"Suppliers updated ({len(ops)} changes)"):
        st.rerun()
