"""Shortages from the latest counts (+ manual needs) → purchase orders grouped by supplier."""
from __future__ import annotations

from collections import Counter
from datetime import date

import pandas as pd
import streamlit as st

from core import config, logic, storage
from ui import common, nav

SHOW = ["Select", "Item", "Br", "Why", "Details", "On hand", "On order", "Last bought", "Qty", "Unit",
        "Brand", "Supplier", "Notes"]


def render():
    common.page_title("Shortages → purchase order", "النواقص ← طلب شراء",
                      "Items marked B (or flagged by the app) in the latest counts. Tick, adjust quantity and "
                      "supplier, add anything else you need, and the app writes one order per supplier.")
    d = common.db()
    sups, sup_by_id, sup_by_name = common.supplier_maps(d, active_only=True)
    sup_names = [""] + [s["name"] for s in sups]
    items, by_id, by_name = common.item_maps(d, active_only=True)

    c = st.columns([1.4, 1.2, 1.2])
    codes = c[0].multiselect("Branches", ["D", "L"], default=["D", "L"],
                             format_func=lambda x: {"D": "Diamond", "L": "La Cité"}[x], key="sh_codes")
    inc_s = c[1].checkbox("Include app suggestions", value=True, key="sh_sugg",
                          help="Items marked G whose lots are all expired, empty or below the minimum")
    inc_e = c[2].checkbox("Include lots expiring soon", value=False, key="sh_exp")
    rows = logic.shortage_rows(d, codes, inc_s, inc_e)

    st.markdown(f"#### From the counts · {len(rows)} items")
    if rows:
        df = pd.DataFrame([{**{k: r.get(k) for k in ("Item", "Why", "Details", "On hand", "On order",
                                                      "Last bought", "Qty", "Unit", "Brand", "Supplier", "Notes")},
                            "Select": bool(r["select"]), "Br": r["Branch"], "item_id": r["item_id"],
                            "count_id": r["count_id"]} for r in rows])
        sig = f"{','.join(codes)}|{inc_s}|{inc_e}|{len(rows)}|{len(d.get('purchase_orders', []))}"
        edited = st.data_editor(
            df, key=f"sh_ed_{abs(hash(sig))}", hide_index=True, use_container_width=True, height=420,
            column_order=SHOW,
            disabled=["Item", "Br", "Why", "Details", "On hand", "On order", "Last bought"],
            column_config={
                "Select": st.column_config.CheckboxColumn("✔", width="small"),
                "Qty": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0, width="small"),
                "Unit": st.column_config.SelectboxColumn("Unit", options=config.UNITS, width="small"),
                "Supplier": st.column_config.SelectboxColumn("Supplier", options=sup_names, width="medium"),
                "On order": st.column_config.NumberColumn("On order", format="%g", width="small",
                                                          help="Still open on earlier purchase orders"),
                "Why": st.column_config.TextColumn("Why", width="small"),
                "Details": st.column_config.TextColumn("Details", width="medium"),
                "On hand": st.column_config.TextColumn("On hand (last count)", width="large"),
            })
    else:
        edited = pd.DataFrame(columns=SHOW + ["item_id", "count_id"])
        st.success("No shortages in the latest counts for these branches.")

    st.markdown("#### Other needs (not in the count)")
    base = pd.DataFrame({"Item": pd.Series(dtype="object"), "Free text item": pd.Series(dtype="object"),
                         "Br": pd.Series(dtype="object"), "Qty": pd.Series(dtype="float64"),
                         "Unit": pd.Series(dtype="object"), "Brand": pd.Series(dtype="object"),
                         "Supplier": pd.Series(dtype="object"), "Notes": pd.Series(dtype="object")})
    manual = st.data_editor(
        base, key=f"sh_manual_{st.session_state.get('sh_manual_ver', 0)}", num_rows="dynamic",
        hide_index=True, use_container_width=True,
        column_config={
            "Item": st.column_config.SelectboxColumn("Catalog item", options=[i["name"] for i in items], width="medium"),
            "Free text item": st.column_config.TextColumn("…or free text", width="medium"),
            "Br": st.column_config.SelectboxColumn("Br", options=["D", "L"], width="small"),
            "Qty": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0, width="small"),
            "Unit": st.column_config.SelectboxColumn("Unit", options=config.UNITS, width="small"),
            "Supplier": st.column_config.SelectboxColumn("Supplier", options=sup_names, width="medium"),
        })

    # ---- collect request lines
    req, problems, count_ids = [], [], set()
    for r in common.records(edited):
        if not r.get("Select"):
            continue
        sup = sup_by_name.get(logic.clean_str(r.get("Supplier")))
        if not sup:
            problems.append(f"{r['Item']}: choose a supplier.")
        count_ids.add(r.get("count_id"))
        req.append({"item_id": r["item_id"], "Item": r["Item"], "Brand": r.get("Brand"), "Qty": r.get("Qty"),
                    "Unit": r.get("Unit"), "Notes": r.get("Notes"), "Branch": r.get("Br"),
                    "supplier_id": sup["id"] if sup else ""})
    for r in common.records(manual):
        name = logic.clean_str(r.get("Item")) or logic.clean_str(r.get("Free text item"))
        if not name:
            continue
        it = by_name.get(logic.clean_str(r.get("Item")))
        sup = sup_by_name.get(logic.clean_str(r.get("Supplier")))
        if not sup and it and it.get("default_supplier_id") in sup_by_id:
            sup = sup_by_id[it["default_supplier_id"]]
        if not sup:
            problems.append(f"{name}: choose a supplier.")
        req.append({"item_id": it["id"] if it else "", "Item": name,
                    "Brand": logic.clean_str(r.get("Brand")) or (it or {}).get("default_brand", ""),
                    "Qty": logic.to_float(r.get("Qty")) or 1,
                    "Unit": logic.clean_str(r.get("Unit")) or (it or {}).get("purchase_unit", "pack"),
                    "Notes": r.get("Notes"), "Branch": logic.clean_str(r.get("Br")) or common.branch_code(),
                    "supplier_id": sup["id"] if sup else ""})

    st.divider()
    st.markdown("#### Order details")
    c = st.columns([1, 1.6, 1.4, 1.4])
    po_date = c[0].date_input("Date", value=date.today(), format="DD/MM/YYYY", key="sh_date")
    flags = c[1].multiselect("Flags", config.PO_FLAGS, default=["Urgent"], key="sh_flags")
    channels = c[2].multiselect("Send by", config.PO_CHANNELS, default=["What's app", "Print"], key="sh_ch")
    signed = c[3].text_input("Inventory manager", key="sh_signed",
                             value=d["settings"].get("inventory_manager_supplies", ""))
    by_sup = Counter(sup_by_id.get(r["supplier_id"], {}).get("name", "(no supplier)") for r in req)
    if req:
        st.markdown(" ".join(f"<span class='oi-badge'>{k}: {v} lines</span>" for k, v in by_sup.items()),
                    unsafe_allow_html=True)
    for p in problems:
        st.warning(p)
    n = len({r["supplier_id"] for r in req})
    if st.button(f"📨 Create {n} purchase order{'s' if n != 1 else ''}", type="primary",
                 disabled=not req or bool(problems)):
        pos = logic.build_pos(req, common.user()["name"], po_date.isoformat(), flags, channels,
                              "Shortage" if count_ids - {None} else "Manual", count_ids - {None})
        for po in pos:
            po["signed_by"] = signed
        ids = common.commit([storage.op_insert("purchase_orders", po, year=po_date.year) for po in pos],
                            f"{len(pos)} purchase order(s) created from shortages")
        if ids:
            st.session_state["sh_manual_ver"] = st.session_state.get("sh_manual_ver", 0) + 1
            st.session_state["po_created"] = ids
            st.rerun()
    created = st.session_state.get("po_created")
    if created:
        st.success("Created: " + ", ".join(created) + " – open **Purchase orders** to print / send on WhatsApp.")
        if st.button("📨 Open purchase orders →"):
            st.session_state.pop("po_created", None)
            common.go(nav.ORDERS, po_sel=created[0])
