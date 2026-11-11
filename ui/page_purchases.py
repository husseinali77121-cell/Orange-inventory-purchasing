"""Purchases (supplier invoices / deliveries) and payments."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import config, logic, storage
from ui import common

INV_COLS = ["Catalog item", "Description", "Category", "Brand", "Kit / pack", "Lot", "Exp", "Qty",
            "Unit price", "Disc %", "Kind", "Notes"]
PAY_COLS = ["Date", "Amount", "Method", "Ref", "Notes"]


# ------------------------------------------------------------------ conversions
def lines_df(lines, by_id) -> pd.DataFrame:
    rows = [{"Catalog item": by_id.get(l.get("item_id"), {}).get("name", ""),
             "Description": l.get("description", ""), "Category": l.get("category", ""),
             "Brand": l.get("brand", ""), "Kit / pack": l.get("kit", ""), "Lot": l.get("lot", ""),
             "Exp": logic.fmt_exp(l.get("exp")), "Qty": logic.to_float(l.get("qty")),
             "Unit price": logic.to_float(l.get("unit_price")), "Disc %": logic.to_float(l.get("discount_pct")),
             "Kind": l.get("kind") or "Purchase", "Notes": l.get("notes", "")} for l in lines]
    df = pd.DataFrame(rows, columns=INV_COLS)
    for c in ("Qty", "Unit price", "Disc %"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in set(INV_COLS) - {"Qty", "Unit price", "Disc %"}:
        df[c] = df[c].astype("object")
    return df


def df_lines(df, by_name) -> tuple[list[dict], list[str]]:
    out, errs = [], []
    for i, r in enumerate(common.records(df), 1):
        it = by_name.get(logic.clean_str(r.get("Catalog item")))
        desc = logic.clean_str(r.get("Description")) or (it or {}).get("name", "")
        if not desc:
            continue
        try:
            exp = logic.parse_exp(r.get("Exp")) or ""
        except ValueError:
            errs.append(f"Row {i} ({desc}): expiry '{r.get('Exp')}' – use MM/YYYY.")
            exp = ""
        out.append({"item_id": it["id"] if it else "", "description": desc,
                    "category": logic.clean_str(r.get("Category")) or
                    config.TYPE_TO_CATEGORY.get((it or {}).get("type", ""), "other"),
                    "brand": logic.clean_str(r.get("Brand")), "kit": logic.clean_str(r.get("Kit / pack")),
                    "lot": logic.clean_str(r.get("Lot")), "exp": exp,
                    "qty": logic.to_float(r.get("Qty")) or 0.0,
                    "unit_price": logic.to_float(r.get("Unit price")) or 0.0,
                    "discount_pct": logic.to_float(r.get("Disc %")) or 0.0,
                    "kind": logic.clean_str(r.get("Kind")) or "Purchase", "notes": logic.clean_str(r.get("Notes"))})
        if not out[-1]["qty"]:
            errs.append(f"Row {i} ({desc}): quantity is empty.")
    return out, errs


def pays_df(pays) -> pd.DataFrame:
    rows = [{"Date": logic.parse_date(p.get("date")), "Amount": logic.to_float(p.get("amount")),
             "Method": p.get("method") or "Other", "Ref": p.get("ref", ""), "Notes": p.get("notes", "")}
            for p in pays]
    df = pd.DataFrame(rows, columns=PAY_COLS)
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").astype("float64")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ("Method", "Ref", "Notes"):
        df[c] = df[c].astype("object")
    return df


def df_pays(df) -> list[dict]:
    out = []
    for r in common.records(df):
        amt = logic.to_float(r.get("Amount"))
        if amt is None:
            continue
        dt = logic.parse_date(r.get("Date")) or date.today()
        out.append({"date": dt.isoformat(), "amount": amt,
                    "method": logic.clean_str(r.get("Method")) or "Other", "ref": logic.clean_str(r.get("Ref")),
                    "notes": logic.clean_str(r.get("Notes"))})
    return out


# ------------------------------------------------------------------ page
def render():
    common.page_title("Purchases & payments", "المشتريات والفواتير والدفع",
                      "Each delivery / supplier invoice with kit type, lot and expiry – and when it was paid.")
    d = common.db()
    draft = st.session_state.get("inv_draft")
    if draft:
        st.info(f"Receiving purchase order **{draft.get('po_id')}** – check prices, kit, lot and expiry, then save.")
        if st.button("← Back without saving"):
            st.session_state.pop("inv_draft", None)
            st.rerun()
        invoice_form(d, draft, "draft", is_new=True)
        return
    ver = st.session_state.get("inv_new_ver", 0)
    blank = {"date": date.today().isoformat(), "supplier_id": "", "branch": common.user()["branch"],
             "invoice_no": "", "po_id": "", "lines": [], "delivery_fee": 0.0, "discount": 0.0,
             "payments": [], "notes": "", "needs_review": False, "source": "app"}
    if st.session_state.get("inv_mode") == "new":
        if st.button("← Back to invoices"):
            st.session_state.pop("inv_mode", None)
            st.rerun()
        st.markdown("#### ➕ New purchase – manual entry (with or without a purchase order)")
        invoice_form(d, blank, f"new{ver}", is_new=True)
        return
    t1, t2, t3 = st.tabs(["🧾 Invoices", "➕ New purchase (manual)", "💳 Payments due"])
    with t1:
        list_tab(d)
    with t2:
        invoice_form(d, blank, f"new{ver}", is_new=True)
    with t3:
        due_tab(d)


def invoice_form(d, inv, key, is_new):
    items, by_id, by_name = common.item_maps(d)
    sups, sup_by_id, sup_by_name = common.supplier_maps(d)
    snames = [s["name"] for s in sups]
    if not snames:
        st.warning("Add a supplier first.")
        return
    cur_sup = sup_by_id.get(inv.get("supplier_id"), {}).get("name")
    c = st.columns([1.5, 1, 1, 1])
    sname = c[0].selectbox("Supplier", snames, index=snames.index(cur_sup) if cur_sup in snames else 0,
                           key=f"if_sup_{key}")
    idate = c[1].date_input("Date received", value=common.date_value(inv.get("date")), format="DD/MM/YYYY",
                            key=f"if_date_{key}")
    branch = c[2].selectbox("Received at", config.BRANCHES,
                            index=config.BRANCHES.index(inv.get("branch")) if inv.get("branch") in config.BRANCHES else 0,
                            format_func=lambda b: config.BRANCH_LABEL[b], key=f"if_br_{key}")
    inv_no = c[3].text_input("Supplier invoice no.", value=inv.get("invoice_no", ""), key=f"if_no_{key}")
    sup = sup_by_name[sname]

    base_lines = inv.get("lines", [])
    if is_new and not inv.get("po_id"):
        open_pos = [p for p in d.get("purchase_orders", [])
                    if p.get("supplier_id") == sup["id"] and p.get("status") in config.PO_OPEN_STATUSES]
        if open_pos:
            c1, c2 = st.columns([2, 1])
            pid = c1.selectbox("Receiving an order? (optional)", [""] + [p["id"] for p in open_pos],
                               key=f"if_po_{key}")
            if pid and c2.button("Load its lines", key=f"if_load_{key}"):
                st.session_state["inv_draft"] = logic.invoice_from_po(d, next(p for p in open_pos if p["id"] == pid))
                st.rerun()
    st.caption("Kind: Purchase · Return (deducted) · Free / Gift (0). Expiry as MM/YYYY. Unit price after "
               "the supplier's price, Disc % for a line discount.")
    ed = st.data_editor(
        lines_df(base_lines, by_id), key=f"if_lines_{key}", num_rows="dynamic", hide_index=True,
        use_container_width=True,
        column_config={
            "Catalog item": st.column_config.SelectboxColumn("Catalog item", options=[i["name"] for i in items],
                                                             width="medium"),
            "Description": st.column_config.TextColumn("Item description", width="medium"),
            "Category": st.column_config.SelectboxColumn("Category", options=config.CATEGORIES, width="small"),
            "Brand": st.column_config.TextColumn("Brand", width="small"),
            "Kit / pack": st.column_config.TextColumn("Kit / pack", width="small", help="e.g. 2x25, 100 T, 3x50 ml"),
            "Lot": st.column_config.TextColumn("Lot", width="small"),
            "Exp": st.column_config.TextColumn("Exp", width="small", help="MM/YYYY"),
            "Qty": st.column_config.NumberColumn("Qty", min_value=0.0, step=1.0, width="small"),
            "Unit price": st.column_config.NumberColumn("Unit price", min_value=0.0, format="%.2f", width="small"),
            "Disc %": st.column_config.NumberColumn("Disc %", min_value=0.0, max_value=100.0, width="small"),
            "Kind": st.column_config.SelectboxColumn("Kind", options=config.LINE_KINDS, width="small"),
            "Notes": st.column_config.TextColumn("Notes", width="medium"),
        })
    lines, errs = df_lines(ed, by_name)
    c = st.columns(3)
    delivery = c[0].number_input("Delivery fee", min_value=0.0, step=10.0,
                                 value=float(logic.to_float(inv.get("delivery_fee")) or 0), key=f"if_del_{key}")
    discount = c[1].number_input("Invoice discount (amount)", min_value=0.0, step=10.0,
                                 value=float(logic.to_float(inv.get("discount")) or 0), key=f"if_disc_{key}")
    notes = c[2].text_input("Notes", value=inv.get("notes", ""), key=f"if_notes_{key}")

    st.markdown("**Payments**")
    ped = st.data_editor(
        pays_df(inv.get("payments", [])), key=f"if_pays_{key}", num_rows="dynamic", hide_index=True,
        use_container_width=True,
        column_config={"Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                       "Amount": st.column_config.NumberColumn("Amount", format="%.2f"),
                       "Method": st.column_config.SelectboxColumn("Method", options=config.PAYMENT_METHODS),
                       "Ref": st.column_config.TextColumn("Ref / transfer no."),
                       "Notes": st.column_config.TextColumn("Notes")})
    pays = df_pays(ped)
    tmp = {"lines": lines, "delivery_fee": delivery, "discount": discount, "payments": pays}
    t = logic.invoice_totals(tmp)
    paid_now = False
    if is_new and not pays:
        c = st.columns([1.2, 1, 1])
        paid_now = c[0].checkbox("Paid in full now", key=f"if_pn_{key}")
        pm = c[1].selectbox("Method", config.PAYMENT_METHODS, key=f"if_pm_{key}", disabled=not paid_now)
        pd_ = c[2].date_input("Paid on", value=idate, format="DD/MM/YYYY", key=f"if_pd_{key}", disabled=not paid_now)
        if paid_now:
            tmp["payments"] = [{"date": pd_.isoformat(), "amount": t["total"], "method": pm, "ref": "", "notes": ""}]
            t = logic.invoice_totals(tmp)
    m = st.columns(4)
    m[0].metric("Total", common.money(t["total"]))
    m[1].metric("Paid", common.money(t["paid"]))
    m[2].metric("Balance", common.money(t["balance"]))
    m[3].metric("Status", t["status"])
    review = False
    if inv.get("needs_review"):
        st.warning("⚠️ Imported from the old Excel – please check: " + " · ".join(inv.get("review_notes", [])))
        review = st.checkbox("I checked this invoice – clear the warning", key=f"if_rev_{key}")
    for e in errs:
        st.warning(e)
    if st.button("💾 Save purchase", type="primary", key=f"if_save_{key}", disabled=not lines):
        if errs:
            st.error("Fix the rows above first – nothing was saved.")
            return
        rec = {**inv, "date": idate.isoformat(), "supplier_id": sup["id"], "supplier_name": sup["name"],
               "branch": branch, "invoice_no": inv_no, "lines": lines, "delivery_fee": delivery,
               "discount": discount, "payments": tmp["payments"], "notes": notes}
        if review:
            rec["needs_review"] = False
            rec["reviewed_by"] = common.user()["name"]
        ops = []
        if is_new:
            rec["created_by"] = common.user()["name"]
            ops.append(storage.op_insert("invoices", rec, year=idate.year))
            if rec.get("po_id"):
                po = next((p for p in d.get("purchase_orders", []) if p["id"] == rec["po_id"]), None)
                if po:
                    ops.append(storage.op_upsert("purchase_orders", logic.apply_receipt_to_po(po, lines)))
        else:
            ops.append(storage.op_upsert("invoices", rec))
        ids = common.commit(ops, f"Purchase from {sup['name']} saved", rec.get("id", ""))
        if ids:
            st.session_state.pop("inv_draft", None)
            if is_new:
                st.session_state["inv_new_ver"] = st.session_state.get("inv_new_ver", 0) + 1
                st.session_state["inv_sel"] = ids[0]
                st.session_state.pop("inv_mode", None)
            st.rerun()


def list_tab(d):
    sups, sup_by_id, _ = common.supplier_maps(d)
    c = st.columns([1.2, 1.3, 1.6, 1])
    with c[0]:
        start, end = common.period_picker("inv_list", "All time")
    sname = c[1].selectbox("Supplier", ["All"] + [s["name"] for s in sups], key="inv_f_sup")
    stt = c[2].multiselect("Status", ["Unpaid", "Partial", "Paid", "Credit"], default=[], key="inv_f_st",
                           placeholder="All statuses")
    only_rev = c[3].checkbox("Needs review", key="inv_f_rev")
    q = st.text_input("Search item / invoice no.", key="inv_f_q")
    sid = next((s["id"] for s in sups if s["name"] == sname), None) if sname != "All" else None
    invs = list(reversed(logic.invoices_in(d, start, end, sid)))
    rows = []
    for inv in invs:
        t = logic.invoice_totals(inv)
        if stt and t["status"] not in stt:
            continue
        if only_rev and not inv.get("needs_review"):
            continue
        if q and q.lower() not in (" ".join(l.get("description", "") for l in inv.get("lines", [])) + " " +
                                   inv.get("invoice_no", "") + " " + inv["id"]).lower():
            continue
        rows.append({"Ref": inv["id"], "Date": logic.fmt_date(inv.get("date")), "Supplier": inv.get("supplier_name"),
                     "No.": inv.get("invoice_no", ""), "Lines": len(inv.get("lines", [])), "Total": t["total"],
                     "Paid": t["paid"], "Balance": t["balance"], "Status": t["status"],
                     "Paid on": logic.fmt_date(t["last_payment"]), "Method": t["methods"],
                     "⚠️": "review" if inv.get("needs_review") else ""})
    m = st.columns(4)
    m[0].metric("Invoices", len(rows))
    m[1].metric("Total", common.money(sum(r["Total"] for r in rows)))
    m[2].metric("Paid", common.money(sum(r["Paid"] for r in rows)))
    m[3].metric("Balance", common.money(sum(r["Balance"] for r in rows)))
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=380,
                 column_config={k: st.column_config.NumberColumn(format="%.2f") for k in ("Total", "Paid", "Balance")})
    if not rows:
        return
    ids = [r["Ref"] for r in rows]
    pre = st.session_state.pop("inv_sel", None)
    if pre in ids:
        st.session_state["inv_open"] = pre
    elif st.session_state.get("inv_open") not in ids:
        st.session_state.pop("inv_open", None)
    label = {r["Ref"]: f"{r['Ref']} · {r['Date']} · {r['Supplier']} · {logic.fmt_num(r['Total'])} · {r['Status']}"
             for r in rows}
    sel = st.selectbox("Open invoice", ids, key="inv_open", format_func=lambda i: label[i])
    inv = next(i for i in invs if i["id"] == sel)
    with st.expander(f"✏️ {sel} – view / edit / add payment", expanded=True):
        if inv.get("import_ref"):
            st.caption(f"Imported from the old purchasing file ({inv['import_ref']}).")
        invoice_form(d, inv, f"e_{sel}_{inv.get('updated_at', '')}", is_new=False)
        if common.is_admin():
            ok = st.checkbox("Confirm delete", key=f"inv_del_ok_{sel}")
            if st.button("🗑️ Delete invoice (admin)", disabled=not ok, key=f"inv_del_{sel}"):
                if common.commit([storage.op_delete("invoices", sel)], f"Invoice {sel} deleted", sel):
                    st.rerun()


def due_tab(d):
    open_inv = []
    for inv in d.get("invoices", []):
        t = logic.invoice_totals(inv)
        if t["status"] in ("Unpaid", "Partial"):
            open_inv.append((inv, t))
    open_inv.sort(key=lambda x: x[0].get("date", ""))
    if not open_inv:
        st.success("Everything is paid.")
        return
    rows = [{"Ref": i["id"], "Date": logic.fmt_date(i.get("date")), "Supplier": i.get("supplier_name"),
             "Total": t["total"], "Paid": t["paid"], "Balance": t["balance"], "Status": t["status"],
             "Imported – check": "⚠️" if i.get("needs_review") else ""} for i, t in open_inv]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                 column_config={k: st.column_config.NumberColumn(format="%.2f") for k in ("Total", "Paid", "Balance")})
    st.markdown("#### Record a payment")
    labels = {i["id"]: f"{i['id']} · {i.get('supplier_name')} · balance {logic.fmt_num(t['balance'])}"
              for i, t in open_inv}
    with st.form("pay_form", clear_on_submit=True):
        c = st.columns([2, 1, 1, 1])
        ref = c[0].selectbox("Invoice", list(labels), format_func=lambda i: labels[i])
        amount = c[1].number_input("Amount (0 = full balance)", min_value=0.0, step=50.0)
        method = c[2].selectbox("Method", config.PAYMENT_METHODS)
        pdate = c[3].date_input("Date", value=date.today(), format="DD/MM/YYYY")
        c = st.columns(2)
        pref = c[0].text_input("Ref / transfer no.")
        pnote = c[1].text_input("Notes")
        ok = st.form_submit_button("💳 Save payment", type="primary")
    if ok:
        inv, t = next(x for x in open_inv if x[0]["id"] == ref)
        amt = amount or t["balance"]
        rec = {**inv, "payments": inv.get("payments", []) + [
            {"date": pdate.isoformat(), "amount": amt, "method": method, "ref": pref, "notes": pnote}]}
        if common.commit([storage.op_upsert("invoices", rec)], f"Payment {logic.fmt_num(amt)} on {ref}", ref):
            st.rerun()
