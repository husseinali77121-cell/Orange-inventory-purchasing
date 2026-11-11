"""Purchase orders: list, print (PDF), send on WhatsApp, edit, receive → invoice."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import config, logic, pdf_reports, storage
from ui import common, nav

LINE_COLS = ["Catalog item", "Required", "Comp.", "Qty", "Unit", "Br", "Notes", "Received"]


def lines_df(lines: list[dict], by_id: dict) -> pd.DataFrame:
    rows = [{"Catalog item": by_id.get(l.get("item_id"), {}).get("name", ""), "Required": l.get("name", ""),
             "Comp.": l.get("brand", ""), "Qty": logic.to_float(l.get("qty")), "Unit": l.get("unit", ""),
             "Br": l.get("branch", ""), "Notes": l.get("notes", ""),
             "Received": logic.to_float(l.get("received_qty")) or 0.0} for l in lines]
    df = pd.DataFrame(rows, columns=LINE_COLS)
    for c in ("Qty", "Received"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ("Catalog item", "Required", "Comp.", "Unit", "Br", "Notes"):
        df[c] = df[c].astype("object")
    return df


def df_lines(df: pd.DataFrame, by_name: dict) -> list[dict]:
    out = []
    for r in common.records(df):
        it = by_name.get(logic.clean_str(r.get("Catalog item")))
        name = logic.clean_str(r.get("Required")) or (it or {}).get("name", "")
        if not name:
            continue
        out.append({"item_id": it["id"] if it else "", "name": name,
                    "brand": logic.clean_str(r.get("Comp.")) or (it or {}).get("default_brand", ""),
                    "qty": logic.to_float(r.get("Qty")) or 1,
                    "unit": logic.clean_str(r.get("Unit")) or (it or {}).get("purchase_unit", ""),
                    "branch": logic.clean_str(r.get("Br")), "notes": logic.clean_str(r.get("Notes")),
                    "received_qty": logic.to_float(r.get("Received")) or 0})
    return out


def editor(df, key, names, disabled_received=True):
    return st.data_editor(
        df, key=key, num_rows="dynamic", hide_index=True, use_container_width=True,
        disabled=["Received"] if disabled_received else [],
        column_config={
            "Catalog item": st.column_config.SelectboxColumn("Catalog item", options=names, width="medium",
                                                             help="Optional – links the line to stock & price history"),
            "Required": st.column_config.TextColumn("Required (printed)", width="medium"),
            "Comp.": st.column_config.TextColumn("Comp. (brand)", width="small"),
            "Qty": st.column_config.NumberColumn("Quantity", min_value=0.0, step=1.0, width="small"),
            "Unit": st.column_config.SelectboxColumn("Unit", options=config.UNITS, width="small"),
            "Br": st.column_config.SelectboxColumn("Br", options=["", "D", "L"], width="small"),
            "Notes": st.column_config.TextColumn("Notes", width="medium"),
            "Received": st.column_config.NumberColumn("Received", width="small"),
        })


def render():
    common.page_title("Purchase orders", "طلبات الشراء",
                      "Printed on the lab's order form with the company name and phone – send by WhatsApp or print.")
    if st.session_state.get("po_mode") == "new":
        if st.button("← Back to the orders list"):
            st.session_state.pop("po_mode", None)
            st.rerun()
        st.markdown("#### ➕ New purchase order – manual entry (any item, not only shortages)")
        new_tab()
        return
    t1, t2 = st.tabs(["📋 Orders", "➕ New order (manual)"])
    with t1:
        list_tab()
    with t2:
        new_tab()


def list_tab():
    d = common.db()
    pos = sorted(d.get("purchase_orders", []), key=lambda p: (p.get("date", ""), p["id"]), reverse=True)
    if not pos:
        st.info("No purchase orders yet. Create one from **Shortages → order** or the **New order** tab.")
        return
    sups, sup_by_id, _ = common.supplier_maps(d)
    c = st.columns([2, 1.4])
    stt = c[0].multiselect("Status", config.PO_STATUSES, default=config.PO_OPEN_STATUSES, key="po_f_status")
    sname = c[1].selectbox("Supplier", ["All"] + [s["name"] for s in sups], key="po_f_sup")
    view = [p for p in pos if p.get("status") in stt and
            (sname == "All" or sup_by_id.get(p.get("supplier_id"), {}).get("name") == sname)]
    rows = [{"Ref": p["id"], "Date": logic.fmt_date(p.get("date")),
             "Supplier": sup_by_id.get(p.get("supplier_id"), {}).get("name", ""),
             "Branch": config.BRANCH_LABEL.get(p.get("branch"), p.get("branch")), "Status": p.get("status"),
             "Lines": len(p.get("lines", [])), "Flags": ", ".join(p.get("flags", [])), "Source": p.get("source", "")}
            for p in view]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=min(400, 40 + 35 * len(rows)))
    ids = [p["id"] for p in view]
    pre = st.session_state.pop("po_sel", None)
    if pre and pre not in ids:
        ids = [pre] + ids
    if not ids:
        st.info("No orders match the filter.")
        return
    if pre:
        st.session_state["po_open"] = pre
    elif st.session_state.get("po_open") not in ids:
        st.session_state.pop("po_open", None)
    names = {p["id"]: f"{p['id']} · {sup_by_id.get(p.get('supplier_id'), {}).get('name', '')} · {p.get('status')}"
             for p in pos}
    sel = st.selectbox("Open order", ids, key="po_open", format_func=lambda i: names.get(i, i))
    po = next(p for p in pos if p["id"] == sel)
    st.divider()
    detail(d, po)


def detail(d, po):
    items, by_id, by_name = common.item_maps(d)
    names = [i["name"] for i in items]
    sup = logic.index(d, "suppliers").get(po.get("supplier_id"), {})
    st.markdown(f"### {po['id']} · {sup.get('name', '(no supplier)')} "
                f"<span class='oi-badge'>{po.get('status')}</span>", unsafe_allow_html=True)
    st.caption(f"Date {logic.fmt_date(po.get('date'))} · branch {config.BRANCH_LABEL.get(po.get('branch'), po.get('branch'))}"
               f" · 📞 {sup.get('phone') or 'no phone saved'} · by {po.get('created_by', '')}")
    text = logic.po_text(po, d)
    phone = sup.get("whatsapp") or sup.get("phone") or ""
    c = st.columns(4)
    with c[0]:
        common.download("🖨️ PDF order form", pdf_reports.po_pdf(po, d), f"{po['id']}_{sup.get('name', '')}.pdf",
                        f"po_pdf_{po['id']}")
    c[1].link_button("💬 Send on WhatsApp", logic.wa_link(phone, text), use_container_width=True,
                     help="Opens WhatsApp with the order text" + ("" if phone else " – no phone saved, pick the chat"))
    if po.get("status") == "Draft" and c[2].button("📤 Mark as sent", use_container_width=True, key=f"sent_{po['id']}"):
        if common.commit([storage.op_upsert("purchase_orders", {**po, "status": "Sent",
                                                                "sent_at": date.today().isoformat()})],
                         f"{po['id']} marked as sent", po["id"]):
            st.rerun()
    if po.get("status") in config.PO_OPEN_STATUSES and \
            c[3].button("📦 Receive → invoice", type="primary", use_container_width=True, key=f"rcv_{po['id']}"):
        common.go(nav.PURCHASES, inv_draft=logic.invoice_from_po(d, po))
    if not phone:
        st.caption("ℹ️ Save the company's phone in **Suppliers** so WhatsApp opens the right chat directly.")
    with st.expander("WhatsApp text (copy)"):
        st.code(text, language=None)

    editable = po.get("status") in ("Draft", "Sent", "Partially received")
    with st.expander("✏️ Edit order", expanded=po.get("status") == "Draft"):
        k = f"{po['id']}_{po.get('updated_at', '')}"
        c = st.columns([1, 1.6, 1.4, 1.2])
        pdate = c[0].date_input("Date", value=common.date_value(po.get("date")), format="DD/MM/YYYY", key=f"pd_{k}")
        flags = c[1].multiselect("Flags", config.PO_FLAGS, default=[f for f in po.get("flags", []) if f in config.PO_FLAGS],
                                 key=f"pf_{k}")
        chans = c[2].multiselect("Send by", config.PO_CHANNELS,
                                 default=[x for x in po.get("channels", []) if x in config.PO_CHANNELS], key=f"pc_{k}")
        status = c[3].selectbox("Status", config.PO_STATUSES, index=config.PO_STATUSES.index(po.get("status", "Draft")),
                                key=f"ps_{k}")
        c = st.columns([1, 1, 2])
        sups, sup_by_id, sup_by_name = common.supplier_maps(d)
        snames = [s["name"] for s in sups]
        sname = c[0].selectbox("Supplier", snames, index=snames.index(sup["name"]) if sup.get("name") in snames else 0,
                               key=f"psu_{k}")
        signed = c[1].text_input("Inventory manager", value=po.get("signed_by", ""), key=f"psg_{k}")
        notes = c[2].text_input("Notes (printed under the table)", value=po.get("notes", ""), key=f"pn_{k}")
        ed = editor(lines_df(po.get("lines", []), by_id), f"pl_{k}", names)
        if st.button("💾 Save order", disabled=not editable and status == po.get("status"), key=f"psave_{k}"):
            lines = df_lines(ed, by_name)
            if not lines:
                st.error("The order has no lines.")
            else:
                rec = {**po, "date": pdate.isoformat(), "flags": flags, "channels": chans, "status": status,
                       "supplier_id": sup_by_name[sname]["id"], "signed_by": signed, "notes": notes, "lines": lines}
                if common.commit([storage.op_upsert("purchase_orders", rec)], f"{po['id']} updated", po["id"]):
                    st.rerun()

    c = st.columns(3)
    if c[0].button("📋 Duplicate as new draft", key=f"dup_{po['id']}", use_container_width=True):
        new = {k2: v for k2, v in po.items() if k2 not in ("id", "created_at", "updated_at", "sent_at")}
        new.update({"date": date.today().isoformat(), "status": "Draft", "created_by": common.user()["name"],
                    "lines": [{**l, "received_qty": 0} for l in po.get("lines", [])]})
        ids = common.commit([storage.op_insert("purchase_orders", new, year=date.today().year)],
                            f"Order duplicated from {po['id']}")
        if ids:
            st.session_state["po_sel"] = ids[0]
            st.rerun()
    if po.get("status") not in ("Cancelled", "Received") and \
            c[1].button("🚫 Cancel order", key=f"cxl_{po['id']}", use_container_width=True):
        if common.commit([storage.op_upsert("purchase_orders", {**po, "status": "Cancelled"})],
                         f"{po['id']} cancelled", po["id"]):
            st.rerun()
    if common.is_admin() and po.get("status") in ("Draft", "Cancelled"):
        with c[2].expander("🗑️ Delete (admin)"):
            ok = st.checkbox("Confirm delete", key=f"pdel_ok_{po['id']}")
            if st.button("Delete order", disabled=not ok, key=f"pdel_{po['id']}"):
                if common.commit([storage.op_delete("purchase_orders", po["id"])], f"{po['id']} deleted", po["id"]):
                    st.session_state.pop("po_sel", None)
                    st.rerun()


def new_tab():
    d = common.db()
    items, by_id, by_name = common.item_maps(d, active_only=True)
    sups, sup_by_id, sup_by_name = common.supplier_maps(d, active_only=True)
    if not sups:
        st.warning("Add a supplier first (Suppliers page).")
        return
    ver = st.session_state.get("po_new_ver", 0)
    c = st.columns([1.4, 1, 1])
    sname = c[0].selectbox("To (company)", [s["name"] for s in sups], key=f"pn_sup_{ver}")
    sup = sup_by_name[sname]
    c[0].caption(f"📞 {sup.get('phone') or 'no phone saved – add it in Suppliers'}")
    branch = c[1].selectbox("For branch", [*config.BRANCHES, "All"],
                            index=config.BRANCHES.index(common.user()["branch"]),
                            format_func=lambda b: config.BRANCH_LABEL.get(b, b), key=f"pn_br_{ver}")
    pdate = c[2].date_input("Date", value=date.today(), format="DD/MM/YYYY", key=f"pn_date_{ver}")
    c = st.columns([1.6, 1.4, 1.4])
    flags = c[0].multiselect("Flags", config.PO_FLAGS, default=["Urgent"], key=f"pn_flags_{ver}")
    chans = c[1].multiselect("Send by", config.PO_CHANNELS, default=["What's app", "Print"], key=f"pn_ch_{ver}")
    signed = c[2].text_input("Inventory manager", value=d["settings"].get("inventory_manager_supplies", ""),
                             key=f"pn_sg_{ver}")
    st.caption("Pick a catalog item (keeps stock & price history linked) or just type the name in "
               "**Required**. Brand and unit fill in from the catalog if left empty.")
    base = lines_df([], by_id)
    ed = editor(base, f"pn_lines_{ver}", [i["name"] for i in items])
    notes = st.text_input("Notes", key=f"pn_notes_{ver}")
    lines = df_lines(ed, by_name)
    code = config.BRANCH_CODE.get(branch, "")
    for l in lines:
        l["branch"] = l["branch"] or code
    if st.button(f"💾 Save order ({len(lines)} lines)", type="primary", disabled=not lines):
        rec = {"date": pdate.isoformat(), "supplier_id": sup["id"], "branch": branch, "status": "Draft",
               "flags": flags, "channels": chans, "source": "Manual", "count_ids": [],
               "created_by": common.user()["name"], "signed_by": signed, "notes": notes, "lines": lines}
        ids = common.commit([storage.op_insert("purchase_orders", rec, year=pdate.year)], f"New order to {sname}")
        if ids:
            st.session_state["po_new_ver"] = ver + 1
            st.session_state["po_sel"] = ids[0]
            if st.session_state.pop("po_mode", None):
                st.rerun()                  # straight to the saved order (print / WhatsApp)
            st.success(f"Saved as {ids[0]} – open it in the **Orders** tab to print or send.")
