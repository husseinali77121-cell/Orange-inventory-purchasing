"""Items catalog (supplies & chemicals) + price history."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import build_seed, config, logic, storage
from ui import common

COLS = {"order": "Order", "name": "Name", "group": "Group", "type": "Type", "unit": "Count unit",
        "purchase_unit": "Purchase unit", "default_brand": "Default brand", "default_supplier_id": "Default supplier",
        "branches": "Branches", "min_qty": "Min qty", "reorder_qty": "Reorder qty", "aliases": "Other names",
        "active": "Active", "notes": "Notes"}


def render():
    common.page_title("Items catalog", "الأصناف",
                      "One list for counts, orders and purchases. Min qty = the app flags the item as "
                      "'small amount' (B) at or below it. Reorder qty = suggested order quantity.")
    d = common.db()
    sups, sup_by_id, sup_by_name = common.supplier_maps(d)
    c = st.columns([1, 1, 2])
    group = c[0].selectbox("Group", ["All", *config.GROUPS], key="cat_group")
    show_inactive = c[1].checkbox("Show inactive", key="cat_inact")
    q = c[2].text_input("Search", key="cat_q")
    items = [i for i in logic.sorted_items(d, None if group == "All" else group, active_only=not show_inactive)
             if not q or q.lower() in (i.get("name", "") + " " + i.get("aliases", "")).lower()]
    rows = []
    for i in items:
        r = {v: i.get(k) for k, v in COLS.items()}
        r["Default supplier"] = sup_by_id.get(i.get("default_supplier_id"), {}).get("name", "")
        r["Active"] = bool(i.get("active", True))
        r["id"] = i["id"]
        rows.append(r)
    base = pd.DataFrame(rows, columns=list(COLS.values()) + ["id"])
    for cnum in ("Order", "Min qty", "Reorder qty"):
        base[cnum] = pd.to_numeric(base[cnum], errors="coerce").astype("float64")
    for ctext in set(COLS.values()) - {"Order", "Min qty", "Reorder qty", "Active"}:
        base[ctext] = base[ctext].astype("object")
    base["Active"] = base["Active"].astype(bool)
    stamp = d["audit"][-1]["ts"] if d.get("audit") else ""
    ed = st.data_editor(
        base, key=f"cat_ed_{group}_{show_inactive}_{q}_{stamp}", num_rows="dynamic", hide_index=True,
        use_container_width=True, height=520, column_order=list(COLS.values()),
        column_config={
            "Order": st.column_config.NumberColumn(width="small", format="%d"),
            "Name": st.column_config.TextColumn(required=True, width="medium"),
            "Group": st.column_config.SelectboxColumn(options=config.GROUPS, required=True, width="small"),
            "Type": st.column_config.SelectboxColumn(options=config.ALL_TYPES, width="small"),
            "Count unit": st.column_config.SelectboxColumn(options=config.UNITS, width="small"),
            "Purchase unit": st.column_config.SelectboxColumn(options=config.UNITS, width="small"),
            "Default supplier": st.column_config.SelectboxColumn(options=[""] + [s["name"] for s in sups]),
            "Branches": st.column_config.SelectboxColumn(options=config.LINE_BRANCHES, width="small",
                                                         help="Where the item is used: D, L or D&L"),
            "Min qty": st.column_config.NumberColumn(min_value=0.0, width="small"),
            "Reorder qty": st.column_config.NumberColumn(min_value=0.0, width="small"),
            "Other names": st.column_config.TextColumn(help="Comma separated – used to match old purchase lines"),
            "Active": st.column_config.CheckboxColumn(width="small"),
        })
    if st.button("💾 Save catalog", type="primary"):
        save(d, items, ed, sup_by_name)

    st.divider()
    st.markdown("#### Price history")
    all_items = logic.sorted_items(d, active_only=False)
    name = st.selectbox("Item", [i["name"] for i in all_items], key="ph_item")
    it = next(i for i in all_items if i["name"] == name)
    hist = logic.price_history(d, it["id"])
    if hist:
        df = pd.DataFrame([{"Date": logic.fmt_date(h["date"]), "Supplier": h["supplier"], "Description": h["description"],
                            "Brand": h["brand"], "Kit": h["kit"], "Qty": h["qty"], "Unit price": h["unit_price"],
                            "Invoice": h["invoice"]} for h in hist])
        st.dataframe(df, hide_index=True, use_container_width=True)
        chart = pd.DataFrame({"date": pd.to_datetime([h["date"] for h in hist]),
                              "Unit price": [h["unit_price"] for h in hist]}).set_index("date")
        st.line_chart(chart, color="#E46C0A", height=220)
    else:
        st.caption("No purchases linked to this item yet.")
    if common.is_admin():
        with st.expander("Admin tools"):
            if st.button("Fill empty 'Default supplier' from purchase history"):
                tmp = {**d, "items": [dict(i) for i in d["items"]]}
                n = build_seed.set_default_suppliers(tmp)
                changed = [i for i, o in zip(tmp["items"], d["items"]) if i != o]
                if changed and common.commit([storage.op_upsert("items", i) for i in changed],
                                             f"Default supplier filled for {n} items"):
                    st.rerun()
                elif not changed:
                    st.info("Nothing to fill.")


def save(d, items, ed, sup_by_name):
    rows = [r for r in common.records(ed) if logic.clean_str(r.get("Name"))]
    all_names = {i["name"].lower(): i["id"] for i in d.get("items", [])}
    seen = set()
    errors = []
    for r in rows:
        n = logic.clean_str(r["Name"]).lower()
        if n in seen or (n in all_names and all_names[n] != logic.clean_str(r.get("id"))):
            errors.append(f"'{r['Name']}' already exists.")
        seen.add(n)
    if errors:
        for e in errors:
            st.error(e)
        return
    old = {i["id"]: i for i in items}
    ops, kept = [], set()
    max_order = max([i.get("order", 0) or 0 for i in d.get("items", [])] + [0])
    for r in rows:
        rec = {}
        for k, v in COLS.items():
            val = r.get(v)
            if k in ("order", "min_qty", "reorder_qty"):
                rec[k] = logic.to_float(val) or 0
            elif k == "active":
                rec[k] = bool(val) if val is not None else True
            elif k == "default_supplier_id":
                rec[k] = sup_by_name.get(logic.clean_str(val), {}).get("id", "")
            else:
                rec[k] = logic.clean_str(val)
        rec["branches"] = rec["branches"] or "D&L"
        rec["type"] = rec["type"] or ("Chemical" if rec["group"] == "Chemicals" else "Consumable")
        iid = logic.clean_str(r.get("id"))
        if iid and iid in old:
            kept.add(iid)
            merged = {**old[iid], **rec}
            if merged != old[iid]:
                ops.append(storage.op_upsert("items", merged))
        else:
            max_order += 1
            rec["order"] = rec["order"] or max_order
            ops.append(storage.op_insert("items", rec))
    used = {l.get("item_id") for c in d.get("counts", []) for l in c.get("lines", [])} | \
           {l.get("item_id") for p in d.get("purchase_orders", []) for l in p.get("lines", [])} | \
           {l.get("item_id") for i in d.get("invoices", []) for l in i.get("lines", [])}
    for iid, it in old.items():
        if iid in kept:
            continue
        if iid in used:
            ops.append(storage.op_upsert("items", {**it, "active": False}))
            st.info(f"{it['name']} is used in counts/orders – set to inactive instead of deleting.")
        else:
            ops.append(storage.op_delete("items", iid))
    if not ops:
        st.info("No changes.")
        return
    if common.commit(ops, f"Catalog updated ({len(ops)} changes)"):
        st.rerun()
