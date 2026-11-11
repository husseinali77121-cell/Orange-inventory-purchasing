"""Business rules: expiry, stock status, shortages, purchase orders, invoices, balances."""
from __future__ import annotations

import calendar
import math
import re
import urllib.parse
from collections import defaultdict
from datetime import date, datetime, timedelta

from . import config


# =============================================================== small helpers
def to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            return None
        return float(v)
    s = str(v).strip().replace(",", ".")
    if s in ("", "-", "nan", "None", "NaN"):
        return None
    s = s.replace("½", "0.5").replace("¼", "0.25").replace("¾", "0.75")
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(s)
    except ValueError:
        return None


def clean_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def fmt_num(v, dec: int = 2) -> str:
    f = to_float(v)
    if f is None:
        return ""
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f)):,}"
    return f"{f:,.{dec}f}".rstrip("0").rstrip(".")


def fmt_qty(v) -> str:
    f = to_float(v)
    if f is None:
        return ""
    for frac, sym in ((0.5, "½"), (0.25, "¼"), (0.75, "¾")):
        if abs(f - frac) < 1e-9:
            return sym
    return fmt_num(f, 2)


def parse_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def fmt_date(v) -> str:
    d = parse_date(v)
    return d.strftime("%d/%m/%Y") if d else ""


def today() -> date:
    return date.today()


# =============================================================== expiry (MM/YYYY)
def parse_exp(v) -> str | None:
    """Accepts 11/2026, 11-2026, 2026-11, 2026-11-30, 2027 → 'YYYY-MM'. Raises ValueError."""
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return f"{v.year:04d}-{v.month:02d}"
    s = clean_str(v)
    if s in ("", "-"):
        return None
    s = s.replace(".", "/").replace("\\", "/")
    m = re.fullmatch(r"(\d{1,2})\s*[/-]\s*(\d{4})", s)
    if m:
        mo, yr = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{4})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*\d{1,2})?", s)
        if m:
            yr, mo = int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r"(\d{1,2})\s*[/-]\s*(\d{2})", s)      # 11/26
            if m:
                mo, yr = int(m.group(1)), 2000 + int(m.group(2))
            else:
                m = re.fullmatch(r"(\d{4})", s)                      # 2029 → 12/2029
                if not m:
                    raise ValueError(f"Bad expiry date: {v!r} (use MM/YYYY)")
                yr, mo = int(m.group(1)), 12
    if not (1 <= mo <= 12 and 1990 <= yr <= 2100):
        raise ValueError(f"Bad expiry date: {v!r} (use MM/YYYY)")
    return f"{yr:04d}-{mo:02d}"


def safe_exp(v) -> str | None:
    try:
        return parse_exp(v)
    except ValueError:
        return None


def fmt_exp(v) -> str:
    e = safe_exp(v)
    if not e:
        return ""
    yr, mo = e.split("-")
    return f"{int(mo)}/{yr}"


def exp_end(v) -> date | None:
    e = safe_exp(v)
    if not e:
        return None
    yr, mo = (int(x) for x in e.split("-"))
    return date(yr, mo, calendar.monthrange(yr, mo)[1])


def days_left(v, ref: date | None = None) -> int | None:
    d = exp_end(v)
    if not d:
        return None
    return (d - (ref or today())).days


# =============================================================== lookups
def index(db: dict, coll: str) -> dict:
    return {r["id"]: r for r in db.get(coll, [])}


def supplier_name(db: dict, sid: str | None) -> str:
    if not sid:
        return ""
    for s in db.get("suppliers", []):
        if s["id"] == sid:
            return s.get("name", "")
    return ""


def supplier_by_name(db: dict, name: str) -> dict | None:
    key = norm_key(name)
    for s in db.get("suppliers", []):
        if norm_key(s.get("name", "")) == key:
            return s
        for a in split_aliases(s.get("aliases", "")):
            if norm_key(a) == key:
                return s
    return None


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9؀-ۿ]", "", clean_str(s).lower())


def split_aliases(s) -> list[str]:
    return [a.strip() for a in clean_str(s).split(",") if a.strip()]


def item_branch_codes(item: dict) -> list[str]:
    scope = clean_str(item.get("branches")) or "D&L"
    return ["D", "L"] if scope == "D&L" else [scope]


def line_branch_codes(code: str) -> list[str]:
    code = clean_str(code) or "D&L"
    return ["D", "L"] if code == "D&L" else [code]


def sorted_items(db: dict, group: str | None = None, active_only: bool = True) -> list[dict]:
    items = [i for i in db.get("items", [])
             if (group is None or i.get("group") == group)
             and (not active_only or i.get("active", True))]
    return sorted(items, key=lambda i: (i.get("group", ""), i.get("order", 9999),
                                        i.get("name", "").lower()))


# =============================================================== item matching
_TOKEN_FIX = re.compile(r"(\d)(ml|ul|g|t|kg|l)\b")


def tokens(s: str) -> set[str]:
    s = clean_str(s).lower()
    s = _TOKEN_FIX.sub(r"\1 \2", s)
    s = re.sub(r"[^a-z0-9؀-ۿ]+", " ", s)
    out = set()
    for t in s.split():
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return out


def match_item(description: str, items: list[dict]) -> str | None:
    """Best catalog item for a free-text purchase description (alias tokens ⊆ text)."""
    toks = tokens(description)
    if not toks:
        return None
    best, best_score, tie = None, 0, False
    for it in items:
        phrases = [it.get("name", "")] + split_aliases(it.get("aliases", ""))
        for ph in phrases:
            pt = tokens(ph)
            if pt and pt <= toks:
                score = len(pt) * 10 + len("".join(pt)) / 100
                if score > best_score:
                    best, best_score, tie = it["id"], score, False
                elif score == best_score and best != it["id"]:
                    tie = True
    return None if tie else best


# =============================================================== inventory evaluation
def _lot_parts(line: dict, ref: date) -> list[dict]:
    parts = []
    for place in ("active", "stock"):
        q = to_float(line.get(f"{place}_qty"))
        e = safe_exp(line.get(f"{place}_exp"))
        u = clean_str(line.get(f"{place}_unit"))
        if q is None and not e:
            continue
        end = exp_end(e) if e else None
        parts.append({
            "place": place, "qty": q or 0.0, "unit": u, "exp": e,
            "expired": bool(end and end < ref and (q or 0) > 0),
            "days": (end - ref).days if end else None,
        })
    return parts


def evaluate_lines(lines: list[dict], item: dict | None, ref: date | None = None,
                   warn_days: int = 60) -> dict:
    """Availability + suggested status for one item (all lots/brands of one branch)."""
    ref = ref or today()
    item = item or {}
    min_qty = to_float(item.get("min_qty")) or 0.0
    unit = clean_str(item.get("unit"))
    parts = [p for ln in lines for p in _lot_parts(ln, ref)]
    total = sum(p["qty"] for p in parts)
    valid_parts = [p for p in parts if p["qty"] > 0 and not p["expired"]]
    valid = sum(p["qty"] for p in valid_parts)
    any_expired = any(p["expired"] for p in parts)
    comparable = all(p["unit"] in ("", unit) for p in valid_parts)
    small = bool(min_qty and comparable and 0 < valid <= min_qty)
    soon = [p for p in valid_parts if p["days"] is not None and p["days"] <= warn_days]

    if total <= 0:
        avail = config.AVAIL_NONE
    elif any_expired:
        avail = config.AVAIL_EXPIRED
    elif small:
        avail = config.AVAIL_SMALL
    else:
        avail = config.AVAIL_AVAILABLE

    auto = "B" if (valid <= 0 or small) else "G"
    explicit = [clean_str(ln.get("status")) for ln in lines
                if clean_str(ln.get("status")) not in ("", "Auto")]
    if explicit:
        if "B" in explicit:
            final = "B"
        elif "G" in explicit:
            final = "G"
        else:
            final = "-"
    else:
        final = auto

    reasons = []
    if total <= 0:
        reasons.append("Not available")
    elif valid <= 0:
        reasons.append("All expired")
    elif small:
        reasons.append(f"Small amount (≤ {fmt_num(min_qty)} {unit})")
    if any_expired and valid > 0:
        reasons.append("Some lots expired")
    if soon:
        d = min(p["days"] for p in soon)
        reasons.append(f"Expires in {d} days")
    return {
        "avail": avail, "auto_status": auto, "status": final,
        "total_qty": total, "valid_qty": valid, "any_expired": any_expired,
        "expiring_soon": bool(soon),
        "min_days": min((p["days"] for p in valid_parts if p["days"] is not None),
                        default=None),
        "reasons": reasons, "parts": parts,
    }


def on_hand_text(lines: list[dict]) -> str:
    bits = []
    for ln in lines:
        brand = clean_str(ln.get("brand"))
        sub = []
        for place, label in (("active", "Active"), ("stock", "Stock")):
            q = to_float(ln.get(f"{place}_qty"))
            if q is None:
                continue
            u = clean_str(ln.get(f"{place}_unit"))
            e = fmt_exp(ln.get(f"{place}_exp"))
            sub.append(f"{label} {fmt_qty(q)} {u}".strip() + (f" ({e})" if e else ""))
        if sub:
            bits.append((f"{brand}: " if brand else "") + " · ".join(sub))
    return " | ".join(bits) if bits else "—"


def latest_stock(db: dict, ref: date | None = None, warn_days: int | None = None) -> dict:
    """{(branch_code, item_id): {...}} from the latest count that contains the item."""
    ref = ref or today()
    warn = warn_days if warn_days is not None else int(db["settings"].get("expiry_warn_days", 60))
    items = index(db, "items")
    sessions = sorted(db.get("counts", []), key=lambda c: (c.get("date", ""), c.get("id", "")))
    latest: dict = {}
    for s in sessions:
        per_key = defaultdict(list)
        for ln in s.get("lines", []):
            if not ln.get("item_id"):
                continue
            for code in line_branch_codes(ln.get("branch")):
                per_key[(code, ln["item_id"])].append(ln)
        for key, lns in per_key.items():
            latest[key] = {"count_id": s["id"], "count_date": s.get("date"), "lines": lns,
                           "group": s.get("group")}
    for (code, iid), rec in latest.items():
        rec.update(evaluate_lines(rec["lines"], items.get(iid), ref, warn))
        rec["item"] = items.get(iid, {"id": iid, "name": iid})
        rec["branch"] = code
    return latest


def expiry_alerts(db: dict, days: int | None = None, ref: date | None = None) -> list[dict]:
    ref = ref or today()
    days = days if days is not None else int(db["settings"].get("expiry_warn_days", 60))
    out = []
    seen = set()
    for (code, iid), rec in latest_stock(db, ref, days).items():
        for ln in rec["lines"]:
            for p in _lot_parts(ln, ref):
                if p["qty"] <= 0 or p["days"] is None or p["days"] > days:
                    continue
                key = (ln.get("branch"), iid, ln.get("brand"), p["place"], p["exp"], p["qty"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "Branch": clean_str(ln.get("branch")), "Item": rec["item"].get("name"),
                    "Group": rec["item"].get("group", ""), "Brand": clean_str(ln.get("brand")),
                    "Place": p["place"].title(), "Qty": fmt_qty(p["qty"]) + " " + p["unit"],
                    "Exp": fmt_exp(p["exp"]), "Days left": p["days"],
                    "State": "EXPIRED" if p["days"] < 0 else "Expiring soon",
                    "Count": rec["count_id"],
                })
    return sorted(out, key=lambda r: r["Days left"])


# =============================================================== purchases / invoices
def line_total(line: dict) -> float:
    kind = clean_str(line.get("kind")) or "Purchase"
    qty = to_float(line.get("qty")) or 0.0
    price = to_float(line.get("unit_price")) or 0.0
    disc = to_float(line.get("discount_pct")) or 0.0
    gross = qty * price * (1 - disc / 100.0)
    if kind == "Return":
        return -gross
    if kind in ("Free", "Gift"):
        return 0.0
    return gross


def invoice_totals(inv: dict) -> dict:
    sub = sum(line_total(ln) for ln in inv.get("lines", []))
    delivery = to_float(inv.get("delivery_fee")) or 0.0
    discount = to_float(inv.get("discount")) or 0.0
    total = round(sub + delivery - discount, 2)
    paid = round(sum(to_float(p.get("amount")) or 0.0 for p in inv.get("payments", [])), 2)
    bal = round(total - paid, 2)
    if abs(bal) < 0.5:
        status = "Paid"
    elif bal < 0:
        status = "Credit"
    elif paid > 0:
        status = "Partial"
    else:
        status = "Unpaid"
    last_pay = max((p.get("date", "") for p in inv.get("payments", [])), default="")
    methods = sorted({clean_str(p.get("method")) for p in inv.get("payments", [])
                      if clean_str(p.get("method"))})
    return {"subtotal": round(sub, 2), "delivery": delivery, "discount": discount,
            "total": total, "paid": paid, "balance": bal, "status": status,
            "last_payment": last_pay, "methods": ", ".join(methods)}


def invoices_in(db: dict, start: date | None = None, end: date | None = None,
                supplier_id: str | None = None) -> list[dict]:
    out = []
    for inv in db.get("invoices", []):
        d = parse_date(inv.get("date"))
        if start and (not d or d < start):
            continue
        if end and (not d or d > end):
            continue
        if supplier_id and inv.get("supplier_id") != supplier_id:
            continue
        out.append(inv)
    return sorted(out, key=lambda i: (i.get("date", ""), i.get("id", "")))


def supplier_balances(db: dict) -> list[dict]:
    agg = defaultdict(lambda: {"invoices": 0, "total": 0.0, "paid": 0.0, "balance": 0.0,
                               "open": 0, "review": 0, "last": ""})
    for inv in db.get("invoices", []):
        t = invoice_totals(inv)
        a = agg[inv.get("supplier_id") or ""]
        a["invoices"] += 1
        a["total"] += t["total"]
        a["paid"] += t["paid"]
        a["balance"] += t["balance"]
        a["open"] += 1 if t["status"] in ("Unpaid", "Partial") else 0
        a["review"] += 1 if inv.get("needs_review") else 0
        a["last"] = max(a["last"], inv.get("date", ""))
    rows = []
    for sid, a in agg.items():
        rows.append({"supplier_id": sid, "Supplier": supplier_name(db, sid) or "(unknown)",
                     "Invoices": a["invoices"], "Purchases": round(a["total"], 2),
                     "Paid": round(a["paid"], 2), "Balance": round(a["balance"], 2),
                     "Open invoices": a["open"], "Needs review": a["review"],
                     "Last invoice": fmt_date(a["last"])})
    return sorted(rows, key=lambda r: -r["Purchases"])


def supplier_statement(db: dict, supplier_id: str, start: date | None = None,
                       end: date | None = None) -> tuple[list[dict], float]:
    """Chronological debit/credit rows with running balance. Returns (rows, opening)."""
    events = []
    for inv in db.get("invoices", []):
        if inv.get("supplier_id") != supplier_id:
            continue
        t = invoice_totals(inv)
        desc = ", ".join(clean_str(l.get("description")) for l in inv.get("lines", [])[:4])
        if len(inv.get("lines", [])) > 4:
            desc += " …"
        events.append({"date": inv.get("date", ""), "ord": 0, "ref": inv["id"],
                       "doc": inv.get("invoice_no", ""),
                       "type": "Invoice" if t["total"] >= 0 else "Return",
                       "desc": desc, "debit": max(t["total"], 0.0),
                       "credit": max(-t["total"], 0.0)})
        for p in inv.get("payments", []):
            if clean_str(p.get("method")) == "Credit note":
                continue          # internal allocation between invoices – nets to zero
            amt = to_float(p.get("amount")) or 0.0
            events.append({"date": p.get("date") or inv.get("date", ""), "ord": 1,
                           "ref": inv["id"], "doc": clean_str(p.get("ref")),
                           "type": "Payment", "desc": clean_str(p.get("method")) +
                           (f" – {clean_str(p.get('notes'))}" if clean_str(p.get("notes")) else ""),
                           "debit": 0.0 if amt >= 0 else -amt, "credit": max(amt, 0.0)})
    events.sort(key=lambda e: (e["date"], e["ord"], e["ref"]))
    opening = 0.0
    rows = []
    bal = 0.0
    for e in events:
        d = parse_date(e["date"])
        if start and d and d < start:
            opening += e["debit"] - e["credit"]
            bal = opening
            continue
        if end and d and d > end:
            continue
        bal += e["debit"] - e["credit"]
        rows.append({**e, "balance": round(bal, 2)})
    return rows, round(opening, 2)


def purchase_lines_flat(db: dict, start=None, end=None, supplier_id=None) -> list[dict]:
    items = index(db, "items")
    out = []
    for inv in invoices_in(db, start, end, supplier_id):
        t = invoice_totals(inv)
        for ln in inv.get("lines", []):
            it = items.get(ln.get("item_id") or "", {})
            out.append({
                "Invoice": inv["id"], "Date": fmt_date(inv.get("date")),
                "Supplier": inv.get("supplier_name") or supplier_name(db, inv.get("supplier_id")),
                "Branch": config.BRANCH_LABEL.get(inv.get("branch", ""), inv.get("branch", "")),
                "Item": clean_str(ln.get("description")) or it.get("name", ""),
                "Catalog item": it.get("name", ""),
                "Category": clean_str(ln.get("category")),
                "Brand": clean_str(ln.get("brand")), "Kit / Pack": clean_str(ln.get("kit")),
                "Lot": clean_str(ln.get("lot")), "Exp": fmt_exp(ln.get("exp")),
                "Kind": clean_str(ln.get("kind")) or "Purchase",
                "Qty": to_float(ln.get("qty")), "Unit price": to_float(ln.get("unit_price")),
                "Disc %": to_float(ln.get("discount_pct")),
                "Line total": round(line_total(ln), 2),
                "Paid on": fmt_date(t["last_payment"]), "Payment": t["methods"],
                "Invoice status": t["status"], "Notes": clean_str(ln.get("notes")),
            })
    return out


def price_history(db: dict, item_id: str) -> list[dict]:
    out = []
    for inv in db.get("invoices", []):
        for ln in inv.get("lines", []):
            if ln.get("item_id") != item_id:
                continue
            if (clean_str(ln.get("kind")) or "Purchase") != "Purchase":
                continue
            out.append({"date": inv.get("date", ""), "supplier_id": inv.get("supplier_id"),
                        "supplier": inv.get("supplier_name") or supplier_name(db, inv.get("supplier_id")),
                        "brand": clean_str(ln.get("brand")), "kit": clean_str(ln.get("kit")),
                        "description": clean_str(ln.get("description")),
                        "qty": to_float(ln.get("qty")), "unit_price": to_float(ln.get("unit_price")),
                        "invoice": inv["id"]})
    return sorted(out, key=lambda r: r["date"])


def last_purchases(db: dict) -> dict:
    """item_id → most recent purchase line (with supplier & price)."""
    best: dict = {}
    for inv in db.get("invoices", []):
        for ln in inv.get("lines", []):
            iid = ln.get("item_id")
            if not iid or (clean_str(ln.get("kind")) or "Purchase") != "Purchase":
                continue
            rec = {"date": inv.get("date", ""), "supplier_id": inv.get("supplier_id"),
                   "supplier": inv.get("supplier_name") or supplier_name(db, inv.get("supplier_id")),
                   "brand": clean_str(ln.get("brand")), "kit": clean_str(ln.get("kit")),
                   "description": clean_str(ln.get("description")),
                   "qty": to_float(ln.get("qty")), "unit_price": to_float(ln.get("unit_price"))}
            if iid not in best or rec["date"] >= best[iid]["date"]:
                best[iid] = rec
    return best


def monthly_spend(db: dict, months: int = 12, ref: date | None = None) -> list[dict]:
    ref = ref or today()
    keys = []
    y, m = ref.year, ref.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    sums = {k: 0.0 for k in keys}
    for inv in db.get("invoices", []):
        k = (inv.get("date") or "")[:7]
        if k in sums:
            sums[k] += invoice_totals(inv)["total"]
    return [{"Month": k, "Spend": round(v, 2)} for k, v in sums.items()]


def spend_by(db: dict, field: str, start=None, end=None) -> list[dict]:
    agg = defaultdict(float)
    for r in purchase_lines_flat(db, start, end):
        agg[r.get(field) or "(none)"] += r["Line total"]
    return sorted([{field: k, "Spend": round(v, 2)} for k, v in agg.items()],
                  key=lambda r: -r["Spend"])


# =============================================================== purchase orders
def open_po_qty(db: dict) -> dict:
    """(branch_code, item_id) → qty still on order."""
    out = defaultdict(float)
    for po in db.get("purchase_orders", []):
        if po.get("status") not in config.PO_OPEN_STATUSES:
            continue
        for ln in po.get("lines", []):
            if not ln.get("item_id"):
                continue
            rem = (to_float(ln.get("qty")) or 0) - (to_float(ln.get("received_qty")) or 0)
            if rem <= 0:
                continue
            code = clean_str(ln.get("branch")) or config.BRANCH_CODE.get(po.get("branch"), "D&L")
            for c in line_branch_codes(code):
                out[(c, ln["item_id"])] += rem
    return out


def shortage_rows(db: dict, branch_codes: list[str], include_suggested: bool = True,
                  include_expiring: bool = True, ref: date | None = None) -> list[dict]:
    ref = ref or today()
    stock = latest_stock(db, ref)
    on_order = open_po_qty(db)
    lastp = last_purchases(db)
    rows = []
    for (code, iid), rec in stock.items():
        if code not in branch_codes:
            continue
        item = rec["item"]
        if not item.get("active", True):
            continue
        why = []
        if rec["status"] == "B":
            why.append("Marked B (need to buy)")
        elif rec["status"] == "-":
            continue
        elif include_suggested and rec["auto_status"] == "B":
            why.append("App suggests B")
        if include_expiring and rec["expiring_soon"] and rec["status"] != "B":
            why.append("Expiring soon")
        if not why:
            continue
        lp = lastp.get(iid, {})
        sid = item.get("default_supplier_id") or lp.get("supplier_id") or ""
        qty = to_float(item.get("reorder_qty")) or lp.get("qty") or 1
        rows.append({
            "select": rec["status"] == "B",
            "item_id": iid, "Item": item.get("name", iid), "Group": item.get("group", ""),
            "Branch": code, "Why": " + ".join(why),
            "Details": "; ".join(rec["reasons"]),
            "On hand": on_hand_text(rec["lines"]),
            "Counted": fmt_date(rec["count_date"]), "count_id": rec["count_id"],
            "On order": on_order.get((code, iid), 0.0),
            "Last bought": (f"{fmt_date(lp['date'])} · {lp['supplier']} · "
                            f"{fmt_num(lp['unit_price'])}" if lp else ""),
            "Qty": float(qty), "Unit": clean_str(item.get("purchase_unit")) or clean_str(item.get("unit")) or "pack",
            "Brand": clean_str(item.get("default_brand")) or lp.get("brand", ""),
            "supplier_id": sid, "Supplier": supplier_name(db, sid),
            "Notes": "",
        })
    return sorted(rows, key=lambda r: (r["Group"], r["Branch"], r["Item"].lower()))


def build_pos(rows: list[dict], user: str, po_date: str, flags: list[str],
              channels: list[str], source: str = "Shortage", count_ids=None) -> list[dict]:
    """Group request lines by supplier → list of PO records (ids assigned on save)."""
    groups = defaultdict(list)
    for r in rows:
        groups[r.get("supplier_id") or ""].append(r)
    pos = []
    for sid, lines in groups.items():
        codes = {clean_str(r.get("Branch")) for r in lines}
        branch = config.CODE_BRANCH.get(codes.pop(), "All") if len(codes) == 1 else "All"
        pos.append({
            "date": po_date, "supplier_id": sid, "branch": branch, "status": "Draft",
            "flags": list(flags), "channels": list(channels), "source": source,
            "count_ids": sorted(set(count_ids or [])), "created_by": user, "notes": "",
            "lines": [{"item_id": r.get("item_id") or "", "name": r.get("Item", ""),
                       "brand": clean_str(r.get("Brand")), "qty": to_float(r.get("Qty")) or 1,
                       "unit": clean_str(r.get("Unit")), "notes": clean_str(r.get("Notes")),
                       "branch": clean_str(r.get("Branch")), "received_qty": 0}
                      for r in lines],
        })
    return pos


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", clean_str(phone).split("-")[0].split("/")[0])
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:      # Egyptian mobile 01xxxxxxxxx
        digits = "20" + digits[1:]
    return digits


def first_phone(s: str) -> str:
    parts = re.split(r"[/,;\-–]| - ", clean_str(s))
    for p in parts:
        d = re.sub(r"\D", "", p)
        if len(d) >= 8:
            return d
    return re.sub(r"\D", "", clean_str(s))


def po_text(po: dict, db: dict) -> str:
    st = db["settings"]
    sup = index(db, "suppliers").get(po.get("supplier_id") or "", {})
    flags = [f for f in po.get("flags", [])]
    lines = [f"*{st.get('lab_name', 'Orange Lab')} – Purchase order {po.get('id', '')}*",
             f"To: {sup.get('name', '')}",
             f"From: {st.get('po_from', '')}" + (
                 f" ({config.BRANCH_LABEL.get(po.get('branch'), po.get('branch'))})"
                 if po.get("branch") in config.BRANCHES else ""),
             f"Date: {fmt_date(po.get('date'))}"]
    if flags:
        lines.append("🔴 " + " / ".join(flags))
    lines.append("")
    for i, ln in enumerate(po.get("lines", []), 1):
        parts = [clean_str(ln.get("name"))]
        if clean_str(ln.get("brand")):
            parts.append(clean_str(ln.get("brand")))
        parts.append(f"{fmt_qty(ln.get('qty'))} {clean_str(ln.get('unit'))}".strip())
        txt = f"{i}. " + " – ".join(parts)
        if clean_str(ln.get("notes")):
            txt += f" ({clean_str(ln.get('notes'))})"
        lines.append(txt)
    if clean_str(po.get("notes")):
        lines += ["", clean_str(po.get("notes"))]
    lines += ["", "We appreciate your cooperation."]
    return "\n".join(lines)


def wa_link(phone: str, text: str) -> str:
    p = normalize_phone(first_phone(phone))
    base = f"https://wa.me/{p}" if p else "https://wa.me/"
    return base + "?text=" + urllib.parse.quote(text)


def po_received_status(po: dict) -> str:
    lines = po.get("lines", [])
    if not lines:
        return po.get("status", "Draft")
    rec = [(to_float(l.get("received_qty")) or 0) for l in lines]
    req = [(to_float(l.get("qty")) or 0) for l in lines]
    if all(r >= q for r, q in zip(rec, req)):
        return "Received"
    if any(r > 0 for r in rec):
        return "Partially received"
    return po.get("status", "Sent")


def invoice_from_po(db: dict, po: dict) -> dict:
    lastp = last_purchases(db)
    items = index(db, "items")
    lines = []
    for ln in po.get("lines", []):
        rem = (to_float(ln.get("qty")) or 0) - (to_float(ln.get("received_qty")) or 0)
        if rem <= 0:
            continue
        it = items.get(ln.get("item_id") or "", {})
        lp = lastp.get(ln.get("item_id") or "", {})
        lines.append({"item_id": ln.get("item_id") or "", "description": ln.get("name", ""),
                      "category": config.TYPE_TO_CATEGORY.get(it.get("type", ""), "other"),
                      "brand": ln.get("brand", ""), "kit": lp.get("kit", ""), "lot": "",
                      "exp": "", "qty": rem, "unit_price": lp.get("unit_price") or 0.0,
                      "discount_pct": 0.0, "kind": "Purchase", "notes": ""})
    return {"date": today().isoformat(), "supplier_id": po.get("supplier_id"),
            "supplier_name": supplier_name(db, po.get("supplier_id")),
            "branch": po.get("branch") if po.get("branch") in config.BRANCHES else "Diamond",
            "invoice_no": "", "po_id": po.get("id"), "lines": lines, "delivery_fee": 0.0,
            "discount": 0.0, "payments": [], "notes": "", "needs_review": False,
            "source": "app"}


def apply_receipt_to_po(po: dict, inv_lines: list[dict]) -> dict:
    """Increase received qty on PO lines (by item id, else by name)."""
    po = {**po, "lines": [dict(l) for l in po.get("lines", [])]}
    for il in inv_lines:
        if (clean_str(il.get("kind")) or "Purchase") not in ("Purchase", "Free", "Gift"):
            continue
        q = to_float(il.get("qty")) or 0
        for pl in po["lines"]:
            same = (il.get("item_id") and pl.get("item_id") == il.get("item_id")) or \
                   (not il.get("item_id") and norm_key(pl.get("name")) == norm_key(il.get("description")))
            if same:
                pl["received_qty"] = (to_float(pl.get("received_qty")) or 0) + q
                break
    po["status"] = po_received_status(po)
    return po


# =============================================================== counts
def blank_line(item: dict, code: str) -> dict:
    return {"item_id": item["id"], "branch": code, "brand": clean_str(item.get("default_brand")),
            "active_qty": None, "active_unit": clean_str(item.get("unit")), "active_exp": "",
            "stock_qty": None, "stock_unit": clean_str(item.get("unit")), "stock_exp": "",
            "status": "Auto", "remark": ""}


def lines_for_new_count(db: dict, group: str, branch: str, copy_last: bool) -> list[dict]:
    codes = ["D", "L"] if branch == "All" else [config.BRANCH_CODE[branch]]
    stock = latest_stock(db) if copy_last else {}
    out = []
    for it in sorted_items(db, group):
        for code in item_branch_codes(it):
            if code not in codes:
                continue
            prev = stock.get((code, it["id"]))
            if prev:
                for ln in prev["lines"]:
                    if clean_str(ln.get("branch")) == "D&L" and code == "L" and "D" in codes:
                        continue              # shared line already added for D
                    out.append({**blank_line(it, code), **{k: ln.get(k) for k in (
                        "brand", "active_qty", "active_unit", "active_exp", "stock_qty",
                        "stock_unit", "stock_exp", "status", "remark")},
                        "branch": clean_str(ln.get("branch")) or code})
            else:
                out.append(blank_line(it, code))
    return out


def count_summary(session: dict, db: dict, ref: date | None = None) -> dict:
    items = index(db, "items")
    ref = ref or parse_date(session.get("date")) or today()
    warn = int(db["settings"].get("expiry_warn_days", 60))
    per = defaultdict(list)
    for ln in session.get("lines", []):
        per[(ln.get("item_id"), clean_str(ln.get("branch")))].append(ln)
    c = {"items": len(per), "B": 0, "expired": 0, "none": 0, "soon": 0}
    for (iid, _), lns in per.items():
        ev = evaluate_lines(lns, items.get(iid), ref, warn)
        c["B"] += ev["status"] == "B"
        c["expired"] += ev["any_expired"]
        c["none"] += ev["avail"] == config.AVAIL_NONE
        c["soon"] += ev["expiring_soon"]
    return c
