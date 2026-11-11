"""Inventory count entry (supplies & chemicals), history and current stock."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import config, excel_reports, importers, logic, pdf_reports, storage
from ui import common, nav

LINE_COLS = ["Item", "Br", "Brand", "Active Qty", "Active Unit", "Active Exp", "Stock Qty", "Stock Unit",
             "Stock Exp", "Status", "Remark"]


# ------------------------------------------------------------------ conversions
def lines_to_df(lines: list[dict], by_id: dict) -> pd.DataFrame:
    rows = []
    for l in lines:
        rows.append({
            "Item": by_id.get(l.get("item_id"), {}).get("name", ""),
            "Br": logic.clean_str(l.get("branch")) or "D",
            "Brand": logic.clean_str(l.get("brand")),
            "Active Qty": logic.to_float(l.get("active_qty")),
            "Active Unit": logic.clean_str(l.get("active_unit")),
            "Active Exp": logic.fmt_exp(l.get("active_exp")),
            "Stock Qty": logic.to_float(l.get("stock_qty")),
            "Stock Unit": logic.clean_str(l.get("stock_unit")),
            "Stock Exp": logic.fmt_exp(l.get("stock_exp")),
            "Status": logic.clean_str(l.get("status")) or "Auto",
            "Remark": logic.clean_str(l.get("remark")),
        })
    df = pd.DataFrame(rows, columns=LINE_COLS)
    for c in ("Active Qty", "Stock Qty"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ("Item", "Br", "Brand", "Active Unit", "Active Exp", "Stock Unit", "Stock Exp", "Status", "Remark"):
        df[c] = df[c].astype("object")
    return df


def df_to_lines(df: pd.DataFrame, by_name: dict) -> tuple[list[dict], list[str]]:
    lines, errors = [], []
    for i, r in enumerate(common.records(df), 1):
        name = logic.clean_str(r.get("Item"))
        has_data = any(logic.clean_str(r.get(k)) for k in LINE_COLS if k not in ("Item", "Br", "Status"))
        if not name:
            if has_data:
                errors.append(f"Row {i}: choose an item.")
            continue
        it = by_name.get(name)
        if not it:
            errors.append(f"Row {i}: '{name}' is not in the catalog.")
            continue
        ln = {"item_id": it["id"], "branch": logic.clean_str(r.get("Br")) or "D",
              "brand": logic.clean_str(r.get("Brand")),
              "active_qty": logic.to_float(r.get("Active Qty")),
              "active_unit": logic.clean_str(r.get("Active Unit")),
              "stock_qty": logic.to_float(r.get("Stock Qty")),
              "stock_unit": logic.clean_str(r.get("Stock Unit")),
              "status": logic.clean_str(r.get("Status")) or "Auto",
              "remark": logic.clean_str(r.get("Remark"))}
        for key, col in (("active_exp", "Active Exp"), ("stock_exp", "Stock Exp")):
            try:
                ln[key] = logic.parse_exp(r.get(col)) or ""
            except ValueError:
                errors.append(f"Row {i} ({name}): {col} '{r.get(col)}' – write it as MM/YYYY, e.g. 11/2026.")
                ln[key] = ""
        if ln["active_qty"] is not None and ln["active_qty"] < 0 or \
                ln["stock_qty"] is not None and ln["stock_qty"] < 0:
            errors.append(f"Row {i} ({name}): quantity cannot be negative.")
        if ln["active_qty"] and not ln["active_unit"]:
            ln["active_unit"] = it.get("unit", "")
        if ln["stock_qty"] and not ln["stock_unit"]:
            ln["stock_unit"] = ln["active_unit"] or it.get("unit", "")
        lines.append(ln)
    return lines, errors


def editor_config(names: list[str]) -> dict:
    units = [""] + config.UNITS
    return {
        "Item": st.column_config.SelectboxColumn("Item", options=names, required=True, width="medium"),
        "Br": st.column_config.SelectboxColumn("Br", options=config.LINE_BRANCHES, width="small",
                                               help="D = Diamond, L = La Cité, D&L = shared"),
        "Brand": st.column_config.TextColumn("Comp. (brand)", width="small"),
        "Active Qty": st.column_config.NumberColumn("Active qty", min_value=0.0, step=0.25, width="small",
                                                    help="Opened / in use on the bench"),
        "Active Unit": st.column_config.SelectboxColumn("Unit", options=units, width="small"),
        "Active Exp": st.column_config.TextColumn("Active exp", width="small", help="MM/YYYY e.g. 11/2026"),
        "Stock Qty": st.column_config.NumberColumn("Stock qty", min_value=0.0, step=0.25, width="small",
                                                   help="Unopened, in the store"),
        "Stock Unit": st.column_config.SelectboxColumn("Unit ", options=units, width="small"),
        "Stock Exp": st.column_config.TextColumn("Stock exp", width="small", help="MM/YYYY e.g. 3/2027"),
        "Status": st.column_config.SelectboxColumn("Status", options=config.STATUS_CHOICES, width="small",
                                                   help="Auto = let the app decide · G good · B need to buy · - not used"),
        "Remark": st.column_config.TextColumn("Remark", width="medium"),
    }


# ------------------------------------------------------------------ page
def render():
    common.page_title("Inventory count", "الجرد – مستلزمات وكيماويات",
                      "Enter the count like the paper sheet: Active (opened) and Stock (store) with expiry. "
                      "One row per brand / lot.")
    t1, t2, t3 = st.tabs(["✏️ Count entry", "📚 Count history", "📦 Current stock & expiry"])
    with t1:
        entry_tab()
    with t2:
        history_tab()
    with t3:
        stock_tab()


def _start_ctx(d, group, branch, cdate, counted_by, approved_by, lines, source="app", sid=None, notes="",
               state="Draft"):
    ver = st.session_state.get("count_ver", 0) + 1
    st.session_state["count_ver"] = ver
    _, by_id, _ = common.item_maps(d)
    st.session_state["count_ctx"] = {
        "id": sid, "group": group, "branch": branch, "date": cdate, "counted_by": counted_by,
        "approved_by": approved_by, "notes": notes, "source": source, "state": state,
        "df": lines_to_df(lines, by_id), "ver": ver}


def entry_tab():
    d = common.db()
    msg = st.session_state.pop("count_msg", None)
    if msg:
        st.info(msg)
    ctx = st.session_state.get("count_ctx")
    saved = st.session_state.get("count_saved_id")
    if saved and not ctx:
        sess = next((c for c in d.get("counts", []) if c["id"] == saved), None)
        if sess:
            st.success(f"Count **{saved}** saved ({sess.get('state')}).")
            c = st.columns(3)
            with c[0]:
                common.download("⬇️ PDF (paper layout)", pdf_reports.count_pdf(sess, d),
                                f"Inventory_{sess['group']}_{sess['date']}.pdf", "dl_saved_pdf")
            with c[1]:
                common.download("⬇️ Excel", excel_reports.count_xlsx(sess, d),
                                f"Inventory_{sess['group']}_{sess['date']}.xlsx", "dl_saved_xlsx", "xlsx")
            with c[2]:
                if st.button("🛒 Shortages → order", use_container_width=True, type="primary"):
                    st.session_state.pop("count_saved_id", None)
                    common.go(nav.SHORTAGES)
            if st.button("Start another count"):
                st.session_state.pop("count_saved_id", None)
                st.rerun()
            return
    if not ctx:
        start_form(d)
        return
    edit_form(d, ctx)


def start_form(d):
    st.markdown("#### Start a count")
    u = common.user()
    c1, c2, c3 = st.columns(3)
    group = c1.radio("What are you counting?", config.GROUPS, horizontal=True, key="cnt_group")
    opts = ["All", *config.BRANCHES] if group == "Chemicals" else [*config.BRANCHES, "All"]
    default = 0 if group == "Chemicals" else opts.index(u["branch"])
    branch = c2.selectbox("Branch", opts, index=default, format_func=lambda b: config.BRANCH_LABEL.get(b, b),
                          key=f"cnt_branch_{group}")
    cdate = c3.date_input("Count date", value=date.today(), format="DD/MM/YYYY", key="cnt_date")
    st_ = d["settings"]
    c1, c2 = st.columns(2)
    counted_by = c1.text_input("Counted by (inventory manager)", key=f"cnt_by_{group}",
                               value=st_.get("inventory_manager_chemicals" if group == "Chemicals"
                                             else "inventory_manager_supplies", ""))
    approved_by = c2.text_input("Approved by (COO)", value=st_.get("coo", ""), key="cnt_appr")
    mode = st.radio("Start from", ["Copy the last count (update only what changed) – recommended",
                                   "Empty sheet from the items catalog",
                                   "Upload a filled Excel template"], key="cnt_mode")
    upload = None
    if mode.startswith("Upload"):
        upload = st.file_uploader("Excel template (.xlsx)", type=["xlsx"], key="cnt_upload")
    b1, b2 = st.columns([1, 1])
    if b1.button("▶️ Start", type="primary", use_container_width=True):
        if mode.startswith("Upload"):
            if not upload:
                st.error("Choose the Excel file first.")
                return
            lines, errs = importers.import_inventory_excel(upload.getvalue(), d)
            for e in errs:
                st.warning(e)
            if not lines:
                st.error("No lines were read from the file.")
                return
            source = f"Excel upload: {upload.name}"
        else:
            lines = logic.lines_for_new_count(d, group, branch, copy_last=mode.startswith("Copy"))
            source = "app"
        _start_ctx(d, group, branch, cdate.isoformat(), counted_by, approved_by, lines, source)
        st.rerun()
    with b2.popover("⬇️ Excel template (offline entry)", use_container_width=True) \
            if hasattr(st, "popover") else b2.expander("⬇️ Excel template (offline entry)"):
        st.caption("Fill it on a PC/phone, then upload it here with 'Upload a filled Excel template'.")
        copy = st.checkbox("Pre-fill with the last count", value=True, key="tpl_copy")
        common.download("Download template", excel_reports.template_xlsx(d, group, branch, copy),
                        f"Inventory_template_{group}_{branch}.xlsx", "dl_tpl", "xlsx")


def edit_form(d, ctx):
    items, by_id, by_name = common.item_maps(d)
    names = [i["name"] for i in items]
    head = f"#### {ctx['group']} · {config.BRANCH_LABEL.get(ctx['branch'], ctx['branch'])}"
    head += f" · editing **{ctx['id']}**" if ctx.get("id") else " · new count"
    st.markdown(head)
    c1, c2, c3, c4 = st.columns([1, 1.3, 1.3, 1])
    ctx["date"] = c1.date_input("Date", value=common.date_value(ctx["date"]), format="DD/MM/YYYY",
                                key=f"ce_date_{ctx['ver']}").isoformat()
    ctx["counted_by"] = c2.text_input("Counted by", value=ctx["counted_by"], key=f"ce_by_{ctx['ver']}")
    ctx["approved_by"] = c3.text_input("Approved by", value=ctx["approved_by"], key=f"ce_ap_{ctx['ver']}")
    c4.markdown(f"<span class='oi-badge'>{ctx.get('state', 'Draft')}</span>", unsafe_allow_html=True)
    st.caption("Tip: add a new row for another brand or lot of the same item (choose the item again). "
               "Expiry as MM/YYYY. Leave Status on **Auto** and the app decides G/B from quantity, minimum "
               "and expiry.")
    edited = st.data_editor(ctx["df"], key=f"count_editor_{ctx['ver']}", num_rows="dynamic",
                            hide_index=True, use_container_width=True, height=560,
                            column_config=editor_config(names))
    lines, errors = df_to_lines(edited, by_name)
    ref = common.date_value(ctx["date"])
    warn = int(d["settings"].get("expiry_warn_days", 60))
    per = {}
    for ln in lines:
        per.setdefault((ln["item_id"], ln["branch"]), []).append(ln)
    checks = []
    nB = nflag = nexp = nnone = 0
    for (iid, br), lns in per.items():
        ev = logic.evaluate_lines(lns, by_id.get(iid), ref, warn)
        nB += ev["status"] == "B"
        nexp += ev["any_expired"]
        nnone += ev["avail"] == config.AVAIL_NONE and ev["status"] != "-"
        if ev["status"] != ev["auto_status"] and ev["status"] != "-":
            nflag += 1
        if ev["status"] == "B" or ev["any_expired"] or ev["expiring_soon"] or \
                (ev["status"] != ev["auto_status"] and ev["status"] != "-"):
            checks.append({"Item": by_id.get(iid, {}).get("name", iid), "Br": br,
                           "Status": common.status_icon(ev["status"]),
                           "App suggests": ev["auto_status"], "Availability": common.avail_icon(ev["avail"]),
                           "Notes": "; ".join(ev["reasons"])})
    m = st.columns(5)
    m[0].metric("Lines", len(lines))
    m[1].metric("Need to buy (B)", nB)
    m[2].metric("Status ≠ app suggestion", nflag)
    m[3].metric("With expired lots", nexp)
    m[4].metric("Not available", nnone)
    if checks:
        with st.expander(f"🔎 Check before saving ({len(checks)} items)", expanded=False):
            st.dataframe(pd.DataFrame(checks), hide_index=True, use_container_width=True)
    for e in errors[:15]:
        st.warning(e)
    ctx["notes"] = st.text_input("Notes", value=ctx.get("notes", ""), key=f"ce_notes_{ctx['ver']}")
    b = st.columns(4)
    draft = b[0].button("💾 Save draft", use_container_width=True)
    final = b[1].button("✅ Save & finalize", type="primary", use_container_width=True)
    cancel = b[3].button("✖️ Close without saving", use_container_width=True)
    if cancel:
        st.session_state.pop("count_ctx", None)
        st.rerun()
    if draft or final:
        if errors:
            st.error("Fix the rows above first – nothing was saved.")
            return
        if not lines:
            st.error("The sheet is empty.")
            return
        rec = {"date": ctx["date"], "group": ctx["group"], "branch": ctx["branch"],
               "counted_by": ctx["counted_by"], "approved_by": ctx["approved_by"], "notes": ctx["notes"],
               "state": "Final" if final else "Draft", "source": ctx.get("source", "app"),
               "created_by": common.user()["name"], "lines": lines}
        if ctx.get("id"):
            rec["id"] = ctx["id"]
            old = next((c for c in d["counts"] if c["id"] == ctx["id"]), {})
            rec["created_by"] = old.get("created_by", rec["created_by"])
            ids = common.commit([storage.op_upsert("counts", rec)], f"Count {ctx['id']} updated", ctx["id"])
        else:
            ids = common.commit([storage.op_insert("counts", rec, year=int(ctx["date"][:4]))],
                                f"New {ctx['group']} count")
        if ids:
            if final:
                st.session_state.pop("count_ctx", None)
                st.session_state["count_saved_id"] = ids[0]
            else:
                ctx["id"] = ids[0]
                ctx["state"] = "Draft"
            st.rerun()


def history_tab():
    d = common.db()
    sessions = sorted(d.get("counts", []), key=lambda c: (c.get("date", ""), c["id"]), reverse=True)
    if not sessions:
        st.info("No counts yet.")
        return
    rows = []
    for s in sessions:
        sm = logic.count_summary(s, d)
        rows.append({"Ref": s["id"], "Date": logic.fmt_date(s.get("date")), "Group": s.get("group"),
                     "Branch": config.BRANCH_LABEL.get(s.get("branch"), s.get("branch")), "State": s.get("state"),
                     "Counted by": s.get("counted_by"), "Items": sm["items"], "B": sm["B"],
                     "Expired lots": sm["expired"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    ref = st.selectbox("Open a count", [s["id"] for s in sessions],
                       format_func=lambda i: next(f"{r['Ref']} · {r['Date']} · {r['Group']} · {r['Branch']}"
                                                  for r in rows if r["Ref"] == i), key="hist_sel")
    sess = next(s for s in sessions if s["id"] == ref)
    c = st.columns(4)
    with c[0]:
        common.download("⬇️ PDF", pdf_reports.count_pdf(sess, d), f"Inventory_{sess['group']}_{sess['date']}.pdf",
                        f"h_pdf_{ref}")
    with c[1]:
        common.download("⬇️ Excel", excel_reports.count_xlsx(sess, d), f"Inventory_{sess['group']}_{sess['date']}.xlsx",
                        f"h_xlsx_{ref}", "xlsx")
    if c[2].button("✏️ Edit this count", use_container_width=True, key=f"h_edit_{ref}"):
        _start_ctx(d, sess["group"], sess["branch"], sess["date"], sess.get("counted_by", ""),
                   sess.get("approved_by", ""), sess.get("lines", []), sess.get("source", "app"), sess["id"],
                   sess.get("notes", ""), sess.get("state", "Draft"))
        st.session_state.pop("count_saved_id", None)
        st.session_state["count_msg"] = f"{ref} is open for editing below."
        st.rerun()
    if c[3].button("📋 Copy as a new count", use_container_width=True, key=f"h_copy_{ref}"):
        _start_ctx(d, sess["group"], sess["branch"], date.today().isoformat(), sess.get("counted_by", ""),
                   sess.get("approved_by", ""), sess.get("lines", []), "app")
        st.session_state.pop("count_saved_id", None)
        st.session_state["count_msg"] = f"New count started from {ref} – update what changed and save."
        st.rerun()
    if common.is_admin():
        with st.expander("🗑️ Delete this count (admin)"):
            sure = st.checkbox(f"Yes, delete {ref} permanently", key=f"h_del_ok_{ref}")
            if st.button("Delete", disabled=not sure, key=f"h_del_{ref}"):
                if common.commit([storage.op_delete("counts", ref)], f"Count {ref} deleted", ref):
                    st.rerun()


def stock_tab():
    d = common.db()
    warn = int(d["settings"].get("expiry_warn_days", 60))
    c = st.columns([1, 1, 1.4, 1.6])
    group = c[0].selectbox("Group", ["All", *config.GROUPS], key="cs_group")
    branch = c[1].selectbox("Branch", ["All", "D", "L"], key="cs_branch",
                            format_func=lambda b: {"All": "All", "D": "Diamond", "L": "La Cité"}[b])
    show = c[2].multiselect("Status", ["B", "G", "-"], default=["B", "G"], key="cs_status")
    q = c[3].text_input("Search item", key="cs_q")
    stock = logic.latest_stock(d)
    rows = []
    for (code, iid), v in stock.items():
        it = v["item"]
        if group != "All" and it.get("group") != group:
            continue
        if branch != "All" and code != branch:
            continue
        if v["status"] not in show:
            continue
        if q and q.lower() not in it.get("name", "").lower():
            continue
        rows.append({"Item": it.get("name"), "Group": it.get("group"), "Br": code,
                     "Status": common.status_icon(v["status"]), "App": v["auto_status"],
                     "Availability": common.avail_icon(v["avail"]), "On hand": logic.on_hand_text(v["lines"]),
                     "Nearest exp (days)": v["min_days"], "Counted": logic.fmt_date(v["count_date"]),
                     "Notes": "; ".join(v["reasons"]), "_o": it.get("order", 9999)})
    rows.sort(key=lambda r: (r["Group"], r["_o"], r["Br"]))
    for r in rows:
        r.pop("_o")
    st.caption(f"{len(rows)} item/branch rows from the latest count of each item.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=430,
                 column_config={"Nearest exp (days)": st.column_config.NumberColumn(format="%d")})
    st.markdown("#### Expiry")
    days = st.slider("Show lots expiring within (days)", 0, 365, warn, step=15, key="cs_days")
    al = logic.expiry_alerts(d, days)
    if al:
        st.dataframe(pd.DataFrame(al), hide_index=True, use_container_width=True)
    else:
        st.success("Nothing expired or expiring in this window.")
    c = st.columns(2)
    with c[0]:
        common.download("⬇️ Expiry report PDF", pdf_reports.expiry_pdf(d, days), f"Expiry_{date.today()}.pdf",
                        "exp_pdf")
    with c[1]:
        common.download("⬇️ Expiry report Excel", excel_reports.expiry_xlsx(d, days), f"Expiry_{date.today()}.xlsx",
                        "exp_xlsx", "xlsx")
