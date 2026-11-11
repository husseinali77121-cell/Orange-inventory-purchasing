"""Dashboard: what needs attention today."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from core import config, logic
from ui import common, nav


def render():
    d = common.db()
    common.page_title("Dashboard", "لوحة المتابعة", "What needs attention – stock, expiry, orders and payments.")
    warn = int(d["settings"].get("expiry_warn_days", 60))
    choice = st.radio("Branch", ["All", *config.BRANCHES], horizontal=True,
                      format_func=lambda b: config.BRANCH_LABEL.get(b, b), key="dash_branch")
    codes = ["D", "L"] if choice == "All" else [config.BRANCH_CODE[choice]]

    stock = {k: v for k, v in logic.latest_stock(d).items() if k[0] in codes and v["item"].get("active", True)}
    need = [v for v in stock.values() if v["status"] == "B"]
    flagged = [v for v in stock.values() if v["status"] == "G" and v["auto_status"] == "B"]
    alerts = [a for a in logic.expiry_alerts(d, warn)
              if any(c in logic.line_branch_codes(a["Branch"]) for c in codes)]
    expired = [a for a in alerts if a["Days left"] < 0]
    soon = [a for a in alerts if a["Days left"] >= 0]
    open_pos = [p for p in d.get("purchase_orders", []) if p.get("status") in config.PO_OPEN_STATUSES]
    invs = d.get("invoices", [])
    due_conf = sum(max(logic.invoice_totals(i)["balance"], 0) for i in invs if not i.get("needs_review"))
    due_rev = sum(max(logic.invoice_totals(i)["balance"], 0) for i in invs if i.get("needs_review"))
    t = date.today()
    month_spend = sum(logic.invoice_totals(i)["total"] for i in invs if (i.get("date") or "")[:7] == t.strftime("%Y-%m"))
    year_spend = sum(logic.invoice_totals(i)["total"] for i in invs if (i.get("date") or "")[:4] == str(t.year))

    c = st.columns(4)
    c[0].metric("🛒 Need to buy (B)", len(need), help="Items marked B in the latest count")
    c[1].metric("🔎 App says buy, sheet says G", len(flagged),
                help="Marked G but every lot is expired / nothing left / below minimum")
    c[2].metric("🔴 Expired lots", len(expired))
    c[3].metric(f"⏳ Expiring ≤ {warn} days", len(soon))
    c = st.columns(4)
    c[0].metric("📨 Open purchase orders", len(open_pos))
    c[1].metric("💳 Due to suppliers", common.money(due_conf),
                help="Unpaid balances of invoices entered or confirmed in the app")
    c[2].metric("⚠️ To review (imported)", common.money(due_rev),
                help="Imported invoices whose payment is not written in the old Excel file")
    c[3].metric(f"💰 Spend {t.strftime('%b %Y')}", common.money(month_spend),
                delta=f"{common.money(year_spend)} this year", delta_color="off")

    st.write("")
    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Need to buy")
        if need or flagged:
            rows = [{"Item": v["item"]["name"], "Br": v["branch"], "Status": common.status_icon(v["status"]),
                     "Why": "; ".join(v["reasons"]) or "marked B", "On hand": logic.on_hand_text(v["lines"]),
                     "Counted": logic.fmt_date(v["count_date"])}
                    for v in sorted(need + flagged, key=lambda v: (v["status"] != "B", v["item"]["name"]))]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=320)
            if st.button("🛒 Turn shortages into purchase orders →", type="primary"):
                common.go(nav.SHORTAGES)
        else:
            st.success("Nothing marked to buy in the latest counts.")
    with right:
        st.subheader("Expiry")
        if alerts:
            st.dataframe(pd.DataFrame(alerts)[["Branch", "Item", "Brand", "Place", "Qty", "Exp", "Days left"]],
                         hide_index=True, use_container_width=True, height=320,
                         column_config={"Days left": st.column_config.NumberColumn(format="%d")})
        else:
            st.success(f"No expired or expiring lots (≤ {warn} days).")

    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Monthly spend")
        ms = pd.DataFrame(logic.monthly_spend(d, 12)).set_index("Month")
        st.bar_chart(ms, color="#E46C0A", height=260)
    with right:
        st.subheader("Open orders & unpaid invoices")
        rows = []
        for p in sorted(open_pos, key=lambda p: p.get("date", "")):
            rows.append({"Ref": p["id"], "Date": logic.fmt_date(p.get("date")),
                         "Supplier": logic.supplier_name(d, p.get("supplier_id")), "State": p.get("status"),
                         "Amount": None})
        for i in sorted(invs, key=lambda i: i.get("date", "")):
            tt = logic.invoice_totals(i)
            if tt["status"] in ("Unpaid", "Partial"):
                rows.append({"Ref": i["id"] + (" ⚠️" if i.get("needs_review") else ""),
                             "Date": logic.fmt_date(i.get("date")),
                             "Supplier": i.get("supplier_name"), "State": tt["status"], "Amount": tt["balance"]})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=260,
                         column_config={"Amount": st.column_config.NumberColumn(format="%.2f")})
        else:
            st.success("No open orders or unpaid invoices.")

    st.subheader("Last counts")
    last = {}
    for cnt in d.get("counts", []):
        k = (cnt.get("group"), cnt.get("branch"))
        if k not in last or cnt.get("date", "") > last[k].get("date", ""):
            last[k] = cnt
    rows = []
    for (g, b), cnt in sorted(last.items()):
        days = (t - (logic.parse_date(cnt.get("date")) or t)).days
        rows.append({"Group": g, "Branch": config.BRANCH_LABEL.get(b, b), "Last count": logic.fmt_date(cnt.get("date")),
                     "Days ago": days, "Ref": cnt["id"], "Reminder": "⏰ count again" if days > 30 else "OK"})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    else:
        st.info("No counts yet – start one in **Inventory count**.")
