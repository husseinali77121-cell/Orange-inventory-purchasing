"""Workload-based consumption check and cost-reduction opportunities.

Approximation used (stated in the app):
  consumption of an item in a period ≈ quantity bought in that period
  (purchases + free/gift − returns, from the Purchases records).
It is normalised by the number of cases (patients) of the same period, so a partial year
can be compared with a full year. Three references, strongest first:
  1. tests performed (if test counts were entered) × (1 + overhead for QC / calibration / repeats)
  2. the item's "expected per case" (e.g. 1 gel tube per patient) – set in the catalog
  3. the item's own usage per 1,000 cases in the baseline period (no setup needed)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from . import config, logic


# =============================================================== workload periods
def periods(db: dict) -> list[dict]:
    """Workload records grouped by (from, to) → one period with cases per branch."""
    groups: dict = {}
    for w in db.get("workload", []):
        k = (w.get("date_from", ""), w.get("date_to", ""))
        p = groups.setdefault(k, {"key": f"{k[0]}|{k[1]}", "label": w.get("label") or f"{k[0]} → {k[1]}",
                                  "from": logic.parse_date(k[0]), "to": logic.parse_date(k[1]),
                                  "cases": defaultdict(float), "tests": defaultdict(lambda: defaultdict(float)),
                                  "ids": []})
        p["cases"][w.get("branch", "")] += logic.to_float(w.get("cases")) or 0
        for t in w.get("tests", []) or []:
            if t.get("item_id"):
                p["tests"][w.get("branch", "")][t["item_id"]] += logic.to_float(t.get("count")) or 0
        p["ids"].append(w["id"])
        if w.get("label"):
            p["label"] = w["label"]
    out = sorted(groups.values(), key=lambda p: (p["from"] or date.min, p["to"] or date.min))
    for p in out:
        p["days"] = ((p["to"] - p["from"]).days + 1) if p["from"] and p["to"] else None
    return out


def cases_in(period: dict, scope: str = "Both") -> float:
    if not period:
        return 0.0
    if scope == "Both":
        return float(sum(period["cases"].values()))
    return float(period["cases"].get(scope, 0))


def tests_in(period: dict, scope: str = "Both") -> dict:
    out = defaultdict(float)
    if not period:
        return out
    for br, tests in period["tests"].items():
        if scope == "Both" or br == scope:
            for iid, n in tests.items():
                out[iid] += n
    return out


# =============================================================== what was bought
def is_equipment(ln: dict, items: dict) -> bool:
    """Analyzers / instruments are capital spend – kept out of consumption and cost per case."""
    if logic.clean_str(ln.get("category")).lower() == "equipment":
        return True
    return items.get(ln.get("item_id") or "", {}).get("type") == "Equipment"



def bought(db: dict, start: date | None, end: date | None, scope: str = "Both") -> dict:
    """Per item: qty (net, purchase units), paid qty, spend; plus totals under key None."""
    catalog = logic.index(db, "items")
    items: dict = defaultdict(lambda: {"qty": 0.0, "qty_paid": 0.0, "spend": 0.0, "lines": 0,
                                       "suppliers": set()})
    tot = {"spend": 0.0, "linked": 0.0, "delivery": 0.0, "invoices": 0, "equipment": 0.0}
    for inv in logic.invoices_in(db, start, end):
        if scope != "Both":
            if inv.get("source") == "import" or inv.get("branch") != scope:
                continue        # imported history has no reliable branch
        t = logic.invoice_totals(inv)
        equip = sum(logic.line_total(l) for l in inv.get("lines", []) if is_equipment(l, catalog))
        tot["spend"] += t["total"] - equip
        tot["equipment"] += equip
        tot["delivery"] += t["delivery"]
        tot["invoices"] += 1
        for ln in inv.get("lines", []):
            iid = ln.get("item_id")
            lt = logic.line_total(ln)
            if not iid or is_equipment(ln, catalog):
                continue
            tot["linked"] += lt
            q = logic.to_float(ln.get("qty")) or 0
            kind = logic.clean_str(ln.get("kind")) or "Purchase"
            rec = items[iid]
            rec["lines"] += 1
            rec["spend"] += lt
            rec["suppliers"].add(inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id")))
            if kind == "Return":
                rec["qty"] -= q
                rec["qty_paid"] -= q
            elif kind == "Purchase":
                rec["qty"] += q
                rec["qty_paid"] += q
            else:                       # free / gift still gets consumed
                rec["qty"] += q
    return {"items": items, "totals": tot}


# =============================================================== consumption check
def consumption(db: dict, cur: dict, base: dict | None = None, scope: str = "Both",
                alert_pct: float = 30, overhead_pct: float = 15) -> dict:
    items = logic.index(db, "items")
    c_cases = cases_in(cur, scope)
    b_cases = cases_in(base, scope) if base else 0.0
    bc = bought(db, cur["from"], cur["to"], scope)
    bb = bought(db, base["from"], base["to"], scope) if base else {"items": {}, "totals": {"spend": 0.0}}
    tests = tests_in(cur, scope)
    stock = logic.latest_stock(db)
    stock_txt = defaultdict(list)
    for (code, iid), v in stock.items():
        if scope == "Both" or config.BRANCH_CODE.get(scope) == code:
            stock_txt[iid].append(f"{code}: {logic.on_hand_text(v['lines'])}")
    rows = []
    for iid in set(bc["items"]) | set(bb["items"]) | set(tests):
        it = items.get(iid, {"name": iid})
        cur_r = bc["items"].get(iid, {"qty": 0.0, "qty_paid": 0.0, "spend": 0.0, "suppliers": set()})
        base_r = bb["items"].get(iid, {"qty": 0.0, "spend": 0.0})
        qc, qb = cur_r["qty"], base_r["qty"]
        price = (cur_r["spend"] / cur_r["qty_paid"]) if cur_r["qty_paid"] > 0 else \
            ((base_r["spend"] / base_r["qty"]) if base_r.get("qty") else 0.0)
        per1k_c = qc / c_cases * 1000 if c_cases else None
        per1k_b = qb / b_cases * 1000 if b_cases and qb > 0 else None
        upp = logic.to_float(it.get("units_per_pack"))
        epc = logic.to_float(it.get("expected_per_case"))
        ref_qty, ref_name, dev = None, "", None
        detail = ""
        few = False
        yield_pct = None
        n_tests = tests.get(iid)
        if n_tests and upp:
            need = n_tests * (1 + overhead_pct / 100) / upp
            ref_qty, ref_name = need, f"{logic.fmt_num(n_tests)} tests + {logic.fmt_num(overhead_pct)}% QC/repeats"
            yield_pct = (n_tests / (qc * upp) * 100) if qc > 0 else None
            detail = f"bought {logic.fmt_num(qc * upp)} tests for {logic.fmt_num(n_tests)} done"
        elif epc and upp and c_cases:
            ref_qty, ref_name = epc * c_cases / upp, f"expected {logic.fmt_num(epc)} {it.get('unit', '')}/case"
            detail = f"{logic.fmt_num(qc * upp / c_cases, 2)} {it.get('unit', '')} per case"
        elif per1k_b is not None and c_cases:
            ref_qty, ref_name = per1k_b * c_cases / 1000, f"same rate as {base['label']}"
            detail = f"{logic.fmt_num(per1k_c, 2)} vs {logic.fmt_num(per1k_b, 2)} per 1,000 cases"
            few = cur_r.get("lines", 0) + base_r.get("lines", 0) < 3
        if ref_qty:
            dev = (qc / ref_qty - 1) * 100
        if dev is None:
            flag = "— no reference" if qc > 0 else "— not bought"
        elif few and abs(dev) > alert_pct:
            flag = "⚪ Few purchases"
        elif dev > alert_pct:
            flag = "🔴 High"
        elif dev < -alert_pct:
            flag = "🟡 Low"
        else:
            flag = "🟢 Normal"
        excess = (qc - ref_qty) if ref_qty is not None else 0.0
        rows.append({
            "item_id": iid, "Item": it.get("name", iid), "Group": it.get("group", ""), "Flag": flag,
            "Bought (cur)": round(qc, 2), "Unit": it.get("purchase_unit", ""),
            "Per 1,000 cases (cur)": round(per1k_c, 2) if per1k_c is not None else None,
            "Per 1,000 cases (base)": round(per1k_b, 2) if per1k_b is not None else None,
            "Reference": ref_name, "Expected qty": round(ref_qty, 2) if ref_qty is not None else None,
            "Deviation %": round(dev, 1) if dev is not None else None,
            "Test yield %": round(yield_pct, 1) if yield_pct is not None else None,
            "Avg price": round(price, 2), "Spend (cur)": round(cur_r["spend"], 2),
            "Excess cost": round(max(excess, 0) * price, 2) if flag == "🔴 High" else 0.0,
            "Purchases (cur/base)": f"{cur_r.get('lines', 0)}/{base_r.get('lines', 0)}",
            "Detail": detail, "Stock now": " | ".join(stock_txt.get(iid, [])),
            "Suppliers": ", ".join(sorted(s for s in cur_r["suppliers"] if s)),
        })
    order = {"🔴 High": 0, "🟡 Low": 1, "⚪ Few purchases": 2, "🟢 Normal": 3}
    rows.sort(key=lambda r: (order.get(r["Flag"], 3), -r["Excess cost"], -r["Spend (cur)"]))
    spend_c = bc["totals"]["spend"]
    spend_b = bb["totals"]["spend"]
    summary = {
        "cases": c_cases, "cases_base": b_cases, "spend": round(spend_c, 2), "spend_base": round(spend_b, 2),
        "cost_per_case": round(spend_c / c_cases, 2) if c_cases else None,
        "cost_per_case_base": round(spend_b / b_cases, 2) if b_cases else None,
        "linked_share": round(bc["totals"]["linked"] / spend_c * 100, 1) if spend_c else None,
        "high": sum(r["Flag"] == "🔴 High" for r in rows), "low": sum(r["Flag"] == "🟡 Low" for r in rows),
        "excess_cost": round(sum(r["Excess cost"] for r in rows), 2),
        "invoices": bc["totals"]["invoices"], "equipment": round(bc["totals"]["equipment"], 2),
    }
    return {"rows": rows, "summary": summary}


# =============================================================== cost reduction
def _desc_key(ln: dict) -> str:
    return "|".join(logic.norm_key(ln.get(k, "")) for k in ("description", "brand", "kit"))


def cost_opportunities(db: dict, start: date | None, end: date | None, cases: float | None = None,
                       cons_rows: list[dict] | None = None, min_saving: float = 50) -> dict:
    items = logic.index(db, "items")
    invs = logic.invoices_in(db, start, end)
    equipment = sum(logic.line_total(l) for i in invs for l in i.get("lines", []) if is_equipment(l, items))
    total = sum(logic.invoice_totals(i)["total"] for i in invs) - equipment

    # -- Pareto (A/B/C) on net spend per item (unlinked lines by their text)
    spend = defaultdict(float)
    qty_by = defaultdict(float)
    for inv in invs:
        for ln in inv.get("lines", []):
            if is_equipment(ln, items):
                continue
            k = items.get(ln.get("item_id") or "", {}).get("name") or f"(not linked) {ln.get('description', '')}"
            spend[k] += logic.line_total(ln)
            if (logic.clean_str(ln.get("kind")) or "Purchase") == "Purchase":
                qty_by[k] += logic.to_float(ln.get("qty")) or 0
    pareto, cum = [], 0.0
    base_total = sum(v for v in spend.values() if v > 0) or 1.0
    for k, v in sorted(spend.items(), key=lambda kv: -kv[1]):
        if v <= 0:
            continue
        before = cum / base_total * 100          # the item that crosses 80 % is still an A item
        cum += v
        pareto.append({"Item": k, "Spend": round(v, 2), "% of spend": round(v / base_total * 100, 1),
                       "Cumulative %": round(cum / base_total * 100, 1),
                       "Class": "A" if before < 80 else ("B" if before < 95 else "C"),
                       "Per case": round(v / cases, 2) if cases else None})

    # -- price history per (item, description/brand/kit)
    hist = defaultdict(list)
    for inv in db.get("invoices", []):
        for ln in inv.get("lines", []):
            if (logic.clean_str(ln.get("kind")) or "Purchase") != "Purchase":
                continue
            p = logic.to_float(ln.get("unit_price")) or 0
            if p <= 0 or not ln.get("item_id"):
                continue
            eff = p * (1 - (logic.to_float(ln.get("discount_pct")) or 0) / 100)
            hist[(ln["item_id"], _desc_key(ln))].append({
                "date": inv.get("date", ""), "price": eff, "qty": logic.to_float(ln.get("qty")) or 0,
                "supplier": inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id")),
                "desc": ln.get("description", ""), "brand": ln.get("brand", ""), "kit": ln.get("kit", "")})
    s_iso = start.isoformat() if start else ""
    e_iso = end.isoformat() if end else "9999"
    price_up, spread = [], []
    for (iid, _), h in hist.items():
        h.sort(key=lambda r: r["date"])
        in_p = [r for r in h if s_iso <= r["date"] <= e_iso]
        if not in_p:
            continue
        q_period = sum(r["qty"] for r in in_p)
        last = in_p[-1]
        before = [r for r in h if r["date"] < in_p[0]["date"]]
        ref = before[-1] if before else in_p[0]
        if ref is not last and last["price"] > ref["price"] * 1.05:
            inc = (last["price"] / ref["price"] - 1) * 100
            price_up.append({"Item": items.get(iid, {}).get("name", iid), "Product": last["desc"],
                             "Was": round(ref["price"], 2), "On": logic.fmt_date(ref["date"]),
                             "Now": round(last["price"], 2), "Since": logic.fmt_date(last["date"]),
                             "Change %": round(inc, 1), "Supplier now": last["supplier"],
                             "Qty in period": round(q_period, 2),
                             "Saving if old price": round((last["price"] - ref["price"]) * q_period, 2)})
        cheapest = min(h, key=lambda r: r["price"])
        if cheapest["supplier"] != last["supplier"] and last["price"] > cheapest["price"] * 1.05:
            spread.append({"Item": items.get(iid, {}).get("name", iid), "Product": last["desc"],
                           "Last price": round(last["price"], 2), "Last supplier": last["supplier"],
                           "Best price": round(cheapest["price"], 2), "Best supplier": cheapest["supplier"],
                           "Best on": logic.fmt_date(cheapest["date"]), "Qty in period": round(q_period, 2),
                           "Possible saving": round((last["price"] - cheapest["price"]) * q_period, 2)})

    # -- expired stock value (latest counts)
    lastp = logic.last_purchases(db)
    waste = []
    today = date.today()
    for (code, iid), v in logic.latest_stock(db).items():
        it = v["item"]
        lp = lastp.get(iid, {})
        cdate = logic.parse_date(v.get("count_date")) or today
        recent = (today - cdate).days <= 45
        price = lp.get("unit_price") or 0
        upp = logic.to_float(it.get("units_per_pack"))
        for ln in v["lines"]:
            for place in ("active", "stock"):
                q = logic.to_float(ln.get(f"{place}_qty")) or 0
                e = logic.exp_end(ln.get(f"{place}_exp"))
                if q <= 0 or not e or e >= today:
                    continue
                at_count = e < cdate
                if not at_count and not recent:
                    continue            # expired after an old count – may have been used up before
                unit = logic.clean_str(ln.get(f"{place}_unit"))
                val = None
                if price:
                    if unit in ("kit", "box", "pack", "bottle", "vial", "roll") and \
                            unit == logic.clean_str(it.get("purchase_unit")):
                        val = q * price
                    elif upp and unit == logic.clean_str(it.get("unit")):
                        val = q / upp * price
                waste.append({"Branch": code, "Item": it.get("name"), "Brand": ln.get("brand", ""),
                              "Qty": f"{logic.fmt_qty(q)} {unit}".strip(), "Expired": logic.fmt_exp(ln.get(f"{place}_exp")),
                              "When": "already expired at the count" if at_count else "expired after the count",
                              "Counted": logic.fmt_date(cdate), "Est. value": round(val, 2) if val else None})
    waste.sort(key=lambda r: -(r["Est. value"] or 0))

    # -- delivery fees per supplier
    dl = defaultdict(lambda: [0, 0.0, 0])
    for inv in invs:
        t = logic.invoice_totals(inv)
        k = inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id"))
        dl[k][2] += 1
        if t["delivery"]:
            dl[k][0] += 1
            dl[k][1] += t["delivery"]
    delivery = [{"Supplier": k, "Invoices": n_inv, "With delivery fee": n, "Delivery fees": round(f, 2),
                 "Saving if orders combined (½)": round(f / 2, 2) if n >= 3 else 0.0}
                for k, (n, f, n_inv) in sorted(dl.items(), key=lambda kv: -kv[1][1]) if f > 0]

    # -- opportunities list
    opp = []
    for r in price_up:
        if r["Saving if old price"] >= min_saving:
            opp.append({"Type": "Price increase", "Item / supplier": r["Item"],
                        "Finding": f"{r['Product']}: {logic.fmt_num(r['Was'])} → {logic.fmt_num(r['Now'])} "
                                   f"(+{r['Change %']}%) at {r['Supplier now']}",
                        "Est. saving": r["Saving if old price"],
                        "Action": "Negotiate back to the old price or ask a second supplier for a quote"})
    for r in spread:
        if r["Possible saving"] >= min_saving:
            opp.append({"Type": "Cheaper supplier", "Item / supplier": r["Item"],
                        "Finding": f"{r['Last supplier']} {logic.fmt_num(r['Last price'])} vs {r['Best supplier']} "
                                   f"{logic.fmt_num(r['Best price'])} ({r['Best on']})",
                        "Est. saving": r["Possible saving"],
                        "Action": "Same product & pack? → order from the cheaper supplier or ask for a price match"})
    for r in cons_rows or []:
        if r.get("Excess cost", 0) >= min_saving:
            opp.append({"Type": "High consumption", "Item / supplier": r["Item"],
                        "Finding": f"{r['Deviation %']}% above reference ({r['Reference']}); {r['Detail']}",
                        "Est. saving": r["Excess cost"],
                        "Action": "Check repeats, QC/calibration frequency, dead volume, expiry losses, "
                                  "stock build-up and items given out"})
    wsum = sum(r["Est. value"] or 0 for r in waste)
    if waste:
        opp.append({"Type": "Expired stock", "Item / supplier": f"{len(waste)} lots",
                    "Finding": "Expired lots found in the counts" + (f" worth ≈ {logic.fmt_num(wsum)}" if wsum else "") +
                               " (value only where the pack size is known)",
                    "Est. saving": round(wsum, 2),
                    "Action": "Smaller packs for slow tests, FEFO, transfer between branches before expiry"})
    for r in delivery:
        if r["Saving if orders combined (½)"] >= min_saving:
            opp.append({"Type": "Delivery fees", "Item / supplier": r["Supplier"],
                        "Finding": f"{r['With delivery fee']} deliveries cost {logic.fmt_num(r['Delivery fees'])}",
                        "Est. saving": r["Saving if orders combined (½)"],
                        "Action": "Combine orders (weekly / every two weeks) or ask for free delivery above an amount"})
    a_items = [r for r in pareto if r["Class"] == "A"]
    if a_items:
        opp.append({"Type": "Focus list (A items)", "Item / supplier": f"{len(a_items)} items",
                    "Finding": f"{len(a_items)} items make ~80% of spend: " +
                               ", ".join(r["Item"] for r in a_items[:6]) + ("…" if len(a_items) > 6 else ""),
                    "Est. saving": round(sum(r["Spend"] for r in a_items) * 0.05, 2),
                    "Action": "Yearly quote / contract for these first – 5% off them is shown as the estimate"})
    linked = sum(v for k, v in spend.items() if not k.startswith("(not linked)"))
    unl = total - linked
    if total and unl / total > 0.05:
        opp.append({"Type": "Data", "Item / supplier": "Not linked lines",
                    "Finding": f"{round(unl / total * 100, 1)}% of spend is on lines not linked to catalog items",
                    "Est. saving": 0.0,
                    "Action": "Pick the catalog item on those lines (or add 'Other names' in the catalog) so they "
                              "enter the analysis"})
    opp.sort(key=lambda r: -r["Est. saving"])
    return {"total": round(total, 2), "cost_per_case": round(total / cases, 2) if cases else None,
            "equipment": round(equipment, 2),
            "opportunities": opp, "pareto": pareto, "price_up": sorted(price_up, key=lambda r: -r["Saving if old price"]),
            "spread": sorted(spread, key=lambda r: -r["Possible saving"]), "waste": waste, "delivery": delivery}


# =============================================================== workload template import
def parse_workload_excel(file, db: dict) -> tuple[list[dict], list[str]]:
    """Sheet 'Cases': Period | From | To | Branch | Cases | Notes ; optional sheet 'Tests':
    Period | From | To | Branch | Test | Count. Returns workload records (no ids) and errors."""
    import io
    import openpyxl
    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(file)
    wb = openpyxl.load_workbook(file, data_only=True)
    errs: list[str] = []
    recs: dict = {}

    def header(ws):
        for r in range(1, min(ws.max_row, 10) + 1):
            vals = [logic.clean_str(c.value).lower() for c in ws[r]]
            if "branch" in vals and ("cases" in vals or "count" in vals):
                return r, {v: i for i, v in enumerate(vals) if v}
        return None, {}

    def br(v):
        s = logic.clean_str(v).lower().replace("é", "e")
        if s in ("d", "diamond", "دايموند"):
            return "Diamond"
        if s in ("l", "la cite", "lacite", "la cité", "لاسيتي", "city", "السيتي"):
            return "La Cite"
        return None

    def dt(v, year_end=False):
        d = logic.parse_date(v)
        if d:
            return d
        s = logic.clean_str(v)
        if s.isdigit() and len(s) == 4:
            return date(int(s), 12, 31) if year_end else date(int(s), 1, 1)
        if isinstance(v, (int, float)) and 1990 < v < 2100:
            return date(int(v), 12, 31) if year_end else date(int(v), 1, 1)
        return None

    ws = wb["Cases"] if "Cases" in wb.sheetnames else wb.worksheets[0]
    h, cols = header(ws)
    if h is None:
        return [], ["Sheet 'Cases' needs a header row with: Period, From, To, Branch, Cases."]
    for r in range(h + 1, ws.max_row + 1):
        row = [c.value for c in ws[r]]

        def g(name):
            i = cols.get(name)
            return row[i] if i is not None and i < len(row) else None
        if not any(v not in (None, "") for v in row):
            continue
        label = logic.clean_str(g("period"))
        f = dt(g("from")) or dt(label)
        t = dt(g("to"), True) or dt(label, True)
        b = br(g("branch"))
        n = logic.to_float(g("cases"))
        if n is None:
            continue
        if not (f and t and b):
            errs.append(f"Cases row {r}: need From/To (or a year in Period) and Branch D / L.")
            continue
        key = (f.isoformat(), t.isoformat(), b)
        whole_year = (f.month, f.day, t.month, t.day) == (1, 1, 12, 31) and f.year == t.year
        if not label:
            label = str(f.year) if whole_year else f"{logic.fmt_date(f)} → {logic.fmt_date(t)}"
        recs[key] = {"label": label,
                     "date_from": key[0], "date_to": key[1], "branch": b, "cases": n,
                     "notes": logic.clean_str(g("notes")), "tests": []}
    if "Tests" in wb.sheetnames:
        ws = wb["Tests"]
        h, cols = header(ws)
        items = db.get("items", [])
        by_name = {logic.norm_key(i["name"]): i for i in items}
        if h is not None:
            for r in range(h + 1, ws.max_row + 1):
                row = [c.value for c in ws[r]]

                def g(name):
                    i = cols.get(name)
                    return row[i] if i is not None and i < len(row) else None
                name = logic.clean_str(g("test"))
                n = logic.to_float(g("count"))
                if not name or n is None:
                    continue
                label = logic.clean_str(g("period"))
                f = dt(g("from")) or dt(label)
                t = dt(g("to"), True) or dt(label, True)
                b = br(g("branch"))
                key = (f.isoformat() if f else "", t.isoformat() if t else "", b)
                if key not in recs:
                    errs.append(f"Tests row {r}: no Cases row for that period/branch.")
                    continue
                it = by_name.get(logic.norm_key(name))
                if not it:
                    iid = logic.match_item(name, items)
                    it = next((x for x in items if x["id"] == iid), None)
                if not it:
                    errs.append(f"Tests row {r}: '{name}' not in the catalog.")
                    continue
                recs[key]["tests"].append({"item_id": it["id"], "count": n})
    return list(recs.values()), errs


def merge_workload_ops(db: dict, new_recs: list[dict]) -> list[dict]:
    """Replace records with the same period + branch, insert the rest."""
    from . import storage
    existing = {(w.get("date_from"), w.get("date_to"), w.get("branch")): w for w in db.get("workload", [])}
    ops = []
    for r in new_recs:
        old = existing.get((r["date_from"], r["date_to"], r["branch"]))
        if old:
            keep_tests = r.get("tests") or old.get("tests", [])
            ops.append(storage.op_upsert("workload", {**old, **r, "tests": keep_tests}))
        else:
            ops.append(storage.op_insert("workload", r))
    return ops

