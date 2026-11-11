"""Cases per year (workload) → abnormal consumption check → cost-reduction opportunities."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from core import analysis, config, excel_reports, logic, storage
from ui import common

MONEY = st.column_config.NumberColumn(format="%.2f")


def render():
    common.page_title("Consumption & cost", "الاستهلاك وتقليل التكلفة",
                      "Enter the number of cases per year for each branch → the app compares what was bought with "
                      "the workload and lists where money can be saved.")
    d = common.db()
    t = st.tabs(["👥 Cases per year", "📈 Consumption check", "✂️ Cost reduction"])
    with t[0]:
        workload_tab(d)
    with t[1]:
        consumption_tab(d)
    with t[2]:
        cost_tab(d)


# ------------------------------------------------------------------ workload
def workload_tab(d):
    st.markdown("#### Annual cases (both branches)")
    this = date.today().year
    with st.form("wl_year", clear_on_submit=False):
        c = st.columns([1, 1.2, 1.2, 2])
        year = c[0].number_input("Year", 2015, 2100, this - 1, step=1)
        n_d = c[1].number_input("Diamond – cases", 0, 10_000_000, 0, step=100)
        n_l = c[2].number_input("La Cité – cases", 0, 10_000_000, 0, step=100)
        note = c[3].text_input("Notes", placeholder="e.g. patients from LIS, home visits included")
        ok = st.form_submit_button("💾 Save year", type="primary")
    if ok:
        recs = [{"label": str(int(year)), "date_from": f"{int(year)}-01-01", "date_to": f"{int(year)}-12-31",
                 "branch": b, "cases": float(n), "notes": note, "tests": []}
                for b, n in (("Diamond", n_d), ("La Cite", n_l)) if n > 0]
        if not recs:
            st.error("Enter the cases of at least one branch.")
        elif common.commit(analysis.merge_workload_ops(d, recs), f"Cases {int(year)} saved"):
            st.rerun()

    with st.expander("Part of a year / any period (e.g. 1/1 → 30/4/2026)"):
        with st.form("wl_period"):
            c = st.columns([1, 1, 1.2, 1, 1])
            f = c[0].date_input("From", value=date(this, 1, 1), format="DD/MM/YYYY")
            to = c[1].date_input("To", value=date.today(), format="DD/MM/YYYY")
            label = c[2].text_input("Name", placeholder=f"{this} Jan–Apr")
            pd_ = c[3].number_input("Diamond", 0, 10_000_000, 0, step=50)
            pl_ = c[4].number_input("La Cité", 0, 10_000_000, 0, step=50)
            ok2 = st.form_submit_button("💾 Save period")
        if ok2:
            if to < f:
                st.error("'To' is before 'From'.")
            else:
                lab = label or f"{logic.fmt_date(f)} → {logic.fmt_date(to)}"
                recs = [{"label": lab, "date_from": f.isoformat(), "date_to": to.isoformat(), "branch": b,
                         "cases": float(n), "notes": "", "tests": []}
                        for b, n in (("Diamond", pd_), ("La Cite", pl_)) if n > 0]
                if recs and common.commit(analysis.merge_workload_ops(d, recs), f"Cases {lab} saved"):
                    st.rerun()

    ps = analysis.periods(d)
    if ps:
        rows = [{"Period": p["label"], "From": logic.fmt_date(p["from"]), "To": logic.fmt_date(p["to"]),
                 "Diamond": p["cases"].get("Diamond", 0), "La Cité": p["cases"].get("La Cite", 0),
                 "Total": analysis.cases_in(p), "Cases / day": round(analysis.cases_in(p) / p["days"], 1) if p["days"] else None,
                 "Tests entered": sum(len(v) for v in p["tests"].values())} for p in ps]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if common.is_admin():
            with st.expander("🗑️ Delete a period (admin)"):
                pk = st.selectbox("Period", [p["key"] for p in ps], key="wl_del_sel",
                                  format_func=lambda k: next(p["label"] for p in ps if p["key"] == k))
                if st.button("Delete period", key="wl_del"):
                    p = next(p for p in ps if p["key"] == pk)
                    if common.commit([storage.op_delete("workload", i) for i in p["ids"]],
                                     f"Cases period {p['label']} deleted"):
                        st.rerun()
    else:
        st.info("No cases entered yet.")

    st.markdown("#### Excel")
    c = st.columns(2)
    years = sorted({p["from"].year for p in ps if p["from"]} | {this - 1, this})
    with c[0]:
        common.download("⬇️ Cases template (Excel)", excel_reports.workload_template_xlsx(d, years),
                        "Cases_template.xlsx", "wl_tpl", "xlsx")
    up = c[1].file_uploader("Upload filled template", type=["xlsx"], key="wl_up")
    if up:
        recs, errs = analysis.parse_workload_excel(up.getvalue(), d)
        for e in errs:
            st.warning(e)
        if recs:
            st.dataframe(pd.DataFrame([{"Period": r["label"], "Branch": r["branch"], "Cases": r["cases"],
                                        "Tests": len(r["tests"])} for r in recs]), hide_index=True)
            if st.button(f"📥 Import {len(recs)} rows", key="wl_imp"):
                if common.commit(analysis.merge_workload_ops(d, recs), f"Cases imported from {up.name}"):
                    st.rerun()

    if ps:
        with st.expander("🧪 Optional: number of tests per test (makes reagent checks exact)"):
            st.caption("From the LIS: e.g. ALT 4,200 tests in 2025 at La Cité. Needs 'Units per pack' (tests per kit) "
                       "in the catalog for that item.")
            c = st.columns(2)
            pk = c[0].selectbox("Period", [p["key"] for p in ps], key="wt_p",
                                format_func=lambda k: next(p["label"] for p in ps if p["key"] == k))
            br = c[1].selectbox("Branch", config.BRANCHES, key="wt_b", format_func=lambda b: config.BRANCH_LABEL[b])
            p = next(p for p in ps if p["key"] == pk)
            rec = next((w for w in d.get("workload", []) if w["id"] in p["ids"] and w.get("branch") == br), None)
            if not rec:
                st.info("Enter the cases of this branch for this period first.")
            else:
                items, by_id, by_name = common.item_maps(d, active_only=True)
                base = pd.DataFrame([{"Test": by_id.get(t["item_id"], {}).get("name", ""),
                                      "Count": logic.to_float(t.get("count"))} for t in rec.get("tests", [])],
                                    columns=["Test", "Count"])
                base["Test"] = base["Test"].astype("object")
                base["Count"] = pd.to_numeric(base["Count"], errors="coerce").astype("float64")
                ed = st.data_editor(base, key=f"wt_ed_{rec['id']}_{rec.get('updated_at', '')}", num_rows="dynamic",
                                    hide_index=True, use_container_width=True, column_config={
                                        "Test": st.column_config.SelectboxColumn(
                                            options=[i["name"] for i in items if i["group"] == "Chemicals"]),
                                        "Count": st.column_config.NumberColumn(min_value=0.0, step=1.0)})
                if st.button("💾 Save tests", key="wt_save"):
                    tests = [{"item_id": by_name[r["Test"]]["id"], "count": logic.to_float(r["Count"]) or 0}
                             for r in common.records(ed) if r.get("Test") in by_name]
                    if common.commit([storage.op_upsert("workload", {**rec, "tests": tests})],
                                     f"Tests saved for {p['label']} {br}"):
                        st.rerun()


# ------------------------------------------------------------------ consumption
def _last_invoice_date(d):
    return max((logic.parse_date(i.get("date")) for i in d.get("invoices", []) if i.get("date")), default=None)


def consumption_tab(d):
    ps = analysis.periods(d)
    if not ps:
        st.info("Enter the cases per year first (first tab).")
        return
    keys = [p["key"] for p in ps]
    label = {p["key"]: p["label"] for p in ps}
    c = st.columns([1.2, 1.2, 1.4])
    cur_k = c[0].selectbox("Period to check", keys, index=len(keys) - 1, key=f"cs_cur_{len(keys)}", format_func=label.get)
    others = ["(none)"] + [k for k in keys if k != cur_k]
    prev = [k for k in keys if k != cur_k and next(p for p in ps if p["key"] == k)["from"] <
            next(p for p in ps if p["key"] == cur_k)["from"]]
    base_k = c[1].selectbox("Compare with (baseline)", others,
                            index=others.index(prev[-1]) if prev else 0, key=f"cs_base_{cur_k}",
                            format_func=lambda k: label.get(k, k))
    scope = c[2].radio("Branches", ["Both", *config.BRANCHES], horizontal=True, key="cs_scope",
                       format_func=lambda b: "Both (recommended)" if b == "Both" else config.BRANCH_LABEL[b])
    if scope != "Both":
        st.caption("ℹ️ One-branch view uses only purchases entered in the app with 'Received at' = that branch "
                   "(the imported old file has no branch).")
    c = st.columns(2)
    alert = c[0].slider("Flag when different from the reference by more than (%)", 10, 100,
                        int(d["settings"].get("consumption_alert_pct", 30)), 5, key="cs_alert")
    over = c[1].slider("Allowance on test counts for QC / calibration / repeats (%)", 0, 50,
                       int(d["settings"].get("reagent_overhead_pct", 15)), 1, key="cs_over")
    cur = next(p for p in ps if p["key"] == cur_k)
    base = next((p for p in ps if p["key"] == base_k), None)
    res = analysis.consumption(d, cur, base, scope, alert, over)
    s = res["summary"]
    last = _last_invoice_date(d)
    if last and cur["to"] and cur["to"] > last + timedelta(days=20):
        st.warning(f"Purchases are recorded only until {logic.fmt_date(last)} but the period ends "
                   f"{logic.fmt_date(cur['to'])} – consumption will look LOW. Use a period that ends near "
                   f"{logic.fmt_date(last)} or record the missing purchases.")
    m = st.columns(5)
    m[0].metric("Cases", logic.fmt_num(s["cases"]),
                delta=f"{logic.fmt_num(s['cases_base'])} in baseline" if base else None, delta_color="off")
    m[1].metric("Spend", common.money(s["spend"]))
    delta = None
    if s["cost_per_case"] and s["cost_per_case_base"]:
        delta = f"{(s['cost_per_case'] / s['cost_per_case_base'] - 1) * 100:+.1f}% vs baseline"
    m[2].metric("Cost per case", common.money(s["cost_per_case"]) if s["cost_per_case"] else "—",
                delta=delta, delta_color="inverse")
    m[3].metric("🔴 High consumption", s["high"])
    m[4].metric("Excess (estimate)", common.money(s["excess_cost"]))
    if s["linked_share"] is not None and s["linked_share"] < 95:
        st.caption(f"{s['linked_share']}% of the spend is linked to catalog items – the rest can't be analysed "
                   "per item (see Cost reduction → Data).")
    only = st.checkbox("Show only flagged items (High / Low)", value=True, key="cs_only")
    rows = [r for r in res["rows"] if not only or r["Flag"] in ("🔴 High", "🟡 Low")]
    if rows:
        st.dataframe(pd.DataFrame(rows).drop(columns=["item_id"]), hide_index=True, use_container_width=True,
                     height=440, column_config={"Avg price": MONEY, "Spend (cur)": MONEY, "Excess cost": MONEY,
                                                "Deviation %": st.column_config.NumberColumn(format="%+.1f%%")})
    else:
        st.success("Nothing flagged with these settings.")
    with st.expander("How is this calculated?"):
        st.markdown(
            "- **Consumption ≈ what was bought** in the period (purchases + free/gift − returns).\n"
            "- It is divided by the number of **cases** of the same period → usage per 1,000 cases, so a part of a "
            "year can be compared with a full year.\n"
            "- **Reference** (strongest available): ① tests done × (1 + allowance) if test counts and *Units per "
            "pack* are entered · ② *Expected per case* from the catalog · ③ the item's own rate in the baseline.\n"
            "- **🔴 High** = more than the reference by the chosen %: look for repeats, QC/calibration too often, "
            "dead volume, expired kits thrown away, stock building up (see *Stock now*), or items going out.\n"
            "- **🟡 Low** = much less: missing invoices, stock used from before, or the test moved to the other "
            "branch / an outside lab.\n"
            "- Big stock bought at the end of a period looks like high consumption – check *Stock now*.")
    common.download("⬇️ Excel (consumption check)",
                    excel_reports.analysis_xlsx(res, None, f"Consumption check – {cur['label']} vs "
                                                           f"{base['label'] if base else 'no baseline'} ({scope})"),
                    f"Consumption_{cur['label']}.xlsx", "cs_x", "xlsx")


# ------------------------------------------------------------------ cost reduction
def cost_tab(d):
    ps = analysis.periods(d)
    opts = [p["key"] for p in ps] + ["custom"]
    label = {p["key"]: p["label"] for p in ps}
    label["custom"] = "Custom dates (no cases)"
    c = st.columns([1.4, 1.4, 1])
    k = c[0].selectbox("Period", opts, index=max(len(opts) - 2, 0), key=f"cr_p_{len(opts)}", format_func=label.get)
    min_saving = c[2].number_input("Hide savings below", 0, 100_000, 50, step=50, key="cr_min")
    cases, cons_rows = None, None
    if k == "custom":
        with c[1]:
            start, end = common.period_picker("cr", "This year")
    else:
        p = next(x for x in ps if x["key"] == k)
        start, end, cases = p["from"], p["to"], analysis.cases_in(p) or None
        prev = [x for x in ps if x["from"] and p["from"] and x["from"] < p["from"]]
        cons_rows = analysis.consumption(d, p, prev[-1] if prev else None,
                                         alert_pct=int(d["settings"].get("consumption_alert_pct", 30)),
                                         overhead_pct=int(d["settings"].get("reagent_overhead_pct", 15)))["rows"]
        c[1].caption(f"{logic.fmt_date(start)} → {logic.fmt_date(end)} · {logic.fmt_num(cases or 0)} cases"
                     + (f" · compared with {prev[-1]['label']}" if prev else ""))
    res = analysis.cost_opportunities(d, start, end, cases, cons_rows, min_saving)
    m = st.columns(4)
    m[0].metric("Spend", common.money(res["total"]))
    m[1].metric("Cost per case", common.money(res["cost_per_case"]) if res["cost_per_case"] else "—")
    m[2].metric("Opportunities", len(res["opportunities"]))
    m[3].metric("Possible saving (estimate)", common.money(sum(o["Est. saving"] for o in res["opportunities"])))
    if res["opportunities"]:
        st.dataframe(pd.DataFrame(res["opportunities"]), hide_index=True, use_container_width=True,
                     column_config={"Est. saving": MONEY, "Finding": st.column_config.TextColumn(width="large"),
                                    "Action": st.column_config.TextColumn(width="large")})
    else:
        st.success("No opportunities above the minimum for this period.")
    st.caption("Estimates for decision support – check pack size and brand before comparing prices.")
    for title, key, money in (("🅰️ Where the money goes (A/B/C)", "pareto", ("Spend", "Per case")),
                              ("📈 Price increases", "price_up", ("Was", "Now", "Saving if old price")),
                              ("🏷️ Cheaper supplier seen before", "spread", ("Last price", "Best price", "Possible saving")),
                              ("🗑️ Expired stock in the latest counts", "waste", ("Est. value",)),
                              ("🚚 Delivery fees", "delivery", ("Delivery fees", "Saving if orders combined (½)"))):
        with st.expander(f"{title} ({len(res[key])})"):
            if res[key]:
                st.dataframe(pd.DataFrame(res[key]), hide_index=True, use_container_width=True,
                             column_config={m_: MONEY for m_ in money})
            else:
                st.caption("Nothing here.")
    common.download("⬇️ Excel (cost reduction)",
                    excel_reports.analysis_xlsx(None, res, f"Cost reduction – {label.get(k, k)}"),
                    f"Cost_reduction_{label.get(k, k)}.xlsx".replace(" ", "_").replace("→", "-").replace("/", "-"),
                    "cr_x", "xlsx")
