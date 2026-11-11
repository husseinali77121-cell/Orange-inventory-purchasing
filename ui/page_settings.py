"""Settings (admin): lab info, data storage & backups, import of the old purchasing file, audit log."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import streamlit as st

from core import build_seed, config, excel_reports, importers, logic, storage
from ui import common


def render():
    common.page_title("Settings", "الإعدادات")
    if not common.is_admin():
        st.info("Settings are for the admin account.")
        return
    d = common.db()
    t = st.tabs(["🏥 Lab & branches", "💾 Data & backup", "📥 Import old purchasing file", "🕘 Activity log"])
    with t[0]:
        lab_tab(d)
    with t[1]:
        data_tab(d)
    with t[2]:
        import_tab(d)
    with t[3]:
        log = list(reversed(d.get("audit", [])))[:300]
        st.dataframe(pd.DataFrame(log), hide_index=True, use_container_width=True, height=500)


def lab_tab(d):
    s = d["settings"]
    with st.form("settings"):
        c = st.columns(3)
        lab = c[0].text_input("Lab name", s.get("lab_name", ""))
        po_from = c[1].text_input("Order form – From", s.get("po_from", ""))
        po_mob = c[2].text_input("Order form – Mob", s.get("po_mobile", ""))
        c = st.columns(3)
        gm = c[0].text_input("General manager", s.get("general_manager", ""))
        coo = c[1].text_input("COO", s.get("coo", ""))
        cur = c[2].text_input("Currency", s.get("currency", "EGP"))
        c = st.columns(3)
        im_s = c[0].text_input("Inventory manager – supplies", s.get("inventory_manager_supplies", ""))
        im_c = c[1].text_input("Inventory manager – chemicals", s.get("inventory_manager_chemicals", ""))
        warn = c[2].number_input("Expiry warning (days)", 7, 365, int(s.get("expiry_warn_days", 60)))
        rows = st.number_input("Numbered rows on the order form", 5, 40, int(s.get("po_blank_rows", 23)))
        branches = {}
        for code in config.BRANCHES:
            b = s.get("branches", {}).get(code, {})
            st.markdown(f"**{config.BRANCH_LABEL[code]}** (printed at the bottom of orders & statements)")
            c = st.columns([2.2, 1])
            branches[code] = {"label": config.BRANCH_LABEL[code],
                              "address_ar": c[0].text_input(f"Address {code}", b.get("address_ar", "")),
                              "phones": c[1].text_input(f"Phones {code}", b.get("phones", ""))}
        if st.form_submit_button("💾 Save settings", type="primary"):
            patch = {"lab_name": lab, "po_from": po_from, "po_mobile": po_mob, "general_manager": gm, "coo": coo,
                     "currency": cur, "inventory_manager_supplies": im_s, "inventory_manager_chemicals": im_c,
                     "expiry_warn_days": int(warn), "po_blank_rows": int(rows), "branches": branches}
            if common.commit([storage.op_settings(patch)], "Settings updated"):
                st.rerun()


def data_tab(d):
    store = common.get_store()
    st.markdown(f"**Storage:** {store.backend.describe()}")
    if store.backend.name == "local":
        st.warning("Local file mode. On Streamlit Cloud local files are lost when the app restarts – add the "
                   "[github] section to the secrets so every save is kept in your GitHub repo.")
    n = {k: len(d.get(k, [])) for k in storage.COLLECTIONS}
    st.caption(" · ".join(f"{k}: {v}" for k, v in n.items()))
    c = st.columns(2)
    with c[0]:
        common.download("⬇️ Full backup (JSON – can be restored)",
                        json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8"),
                        f"orange_inventory_backup_{date.today()}.json", "bk_json", "json")
    with c[1]:
        common.download("⬇️ Everything in Excel (read-only copy)", excel_reports.backup_xlsx(d),
                        f"orange_inventory_{date.today()}.xlsx", "bk_xlsx", "xlsx")
    st.markdown("#### Restore from a JSON backup")
    up = st.file_uploader("Backup file (.json)", type=["json"], key="restore_up")
    if up:
        try:
            new = storage.normalize_db(json.loads(up.getvalue().decode("utf-8")))
            st.caption(" · ".join(f"{k}: {len(new.get(k, []))}" for k in storage.COLLECTIONS))
            ok = st.checkbox("Replace ALL current data with this backup", key="restore_ok")
            if st.button("♻️ Restore", disabled=not ok):
                if common.commit([storage.op_replace_all(new)], "Database restored from backup"):
                    st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"Not a valid backup: {e}")


def import_tab(d):
    st.caption("Reads the 'Annual Kits & consumables Purchasing' workbook (one sheet per year): invoice blocks, "
               "delivery fees, returns (مسترجع), free/gift lines and the payment rows. Already imported rows "
               "are skipped, so you can import an updated file again.")
    up = st.file_uploader("Purchasing workbook (.xlsx)", type=["xlsx"], key="imp_up")
    if not up:
        return
    try:
        invoices, rep = importers.import_legacy_purchases(up.getvalue(), d)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read the file: {e}")
        return
    existing = {i.get("import_key") for i in d.get("invoices", []) if i.get("import_key")}
    new = [i for i in invoices if i["import_key"] not in existing]
    st.success(f"Sheets {', '.join(rep['sheets'])}: {rep['invoices']} invoices found · {len(new)} new.")
    if rep["new_suppliers"]:
        st.info("New suppliers will be added: " + ", ".join(rep["new_suppliers"]))
    if rep["unmatched_items"]:
        st.caption("Lines not linked to a catalog item (kept as text): " + ", ".join(rep["unmatched_items"]))
    if rep["date_fixes"]:
        st.warning("Dates corrected (year typed as the previous year inside a sheet): " + "; ".join(rep["date_fixes"]))
    prev = [{"Date": logic.fmt_date(i["date"]), "Supplier": i["supplier_name"], "Lines": len(i["lines"]),
             "Total": logic.invoice_totals(i)["total"], "Paid": logic.invoice_totals(i)["paid"],
             "Check": "; ".join(i["review_notes"])} for i in new]
    if prev:
        st.dataframe(pd.DataFrame(prev), hide_index=True, use_container_width=True, height=300)
    fill = st.checkbox("Fill empty 'default supplier' of items from this history", value=True, key="imp_fill")
    if st.button(f"📥 Import {len(new)} invoices", type="primary", disabled=not new):
        ops = [storage.op_insert("suppliers", {"name": n, "aliases": "", "phone": "", "whatsapp": "", "email": "",
                                               "contact": "", "address": "", "supplies": "", "payment_terms": "",
                                               "notes": "added by import", "active": True})
               for n in rep["new_suppliers"]]
        if ops and not common.commit(ops, "Suppliers added by import"):
            return
        d2 = common.db()
        importers.link_suppliers(d2, new)
        ops = [storage.op_insert("invoices", i, year=int(i["date"][:4]))
               for i in sorted(new, key=lambda i: (i["date"], i["import_ref"]))]
        if fill:
            tmp = {**d2, "invoices": d2["invoices"] + new, "items": [dict(i) for i in d2["items"]]}
            build_seed.set_default_suppliers(tmp)
            ops += [storage.op_upsert("items", i) for i, o in zip(tmp["items"], d2["items"]) if i != o]
        if common.commit(ops, f"Imported {len(new)} invoices from {up.name}"):
            st.rerun()
