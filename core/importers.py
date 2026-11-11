"""Importers: the lab's existing annual purchasing workbook and the inventory Excel template."""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date, datetime

from . import config, logic

PAY_WORDS = {
    "vodafone": "Vodafone Cash", "vodavone": "Vodafone Cash", "فودافون": "Vodafone Cash",
    "instapay": "InstaPay", "instabay": "InstaPay", "انستا": "InstaPay",
    "cash": "Cash", "نقد": "Cash", "bank": "Bank transfer", "cheque": "Cheque", "شيك": "Cheque",
}


def method_of(text: str) -> str | None:
    t = logic.clean_str(text).lower()
    if not t:
        return None
    for k, v in PAY_WORDS.items():
        if k in t:
            return v
    return None


def category_of(text: str) -> str:
    t = logic.clean_str(text).lower()
    if not t:
        return ""
    if t.startswith("card"):
        return "cards"
    if t.startswith("consum"):
        return "consumables"
    if t.startswith("chem"):
        return "chemical"
    if "analy" in t or "equip" in t:
        return "equipment"
    return t


def _is_delivery(desc: str) -> bool:
    d = desc.lower()
    return any(w in d for w in ("delivary", "delivery", "delivay", "deliv", "توصيل"))


class _Evaluator:
    """Evaluates the simple formulas used in the purchasing sheet (PRODUCT / SUM)."""

    def __init__(self, ws):
        self.ws = ws
        self.cache: dict = {}

    def num(self, coord: str):
        if coord in self.cache:
            return self.cache[coord]
        v = self.ws[coord].value
        out = None
        if isinstance(v, (int, float)):
            out = float(v)
        elif isinstance(v, str) and v.startswith("="):
            out = self._formula(v)
        else:
            out = logic.to_float(v)
        self.cache[coord] = out
        return out

    def _formula(self, f: str):
        f = f.replace(" ", "").upper()
        m = re.fullmatch(r"=PRODUCT\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)", f)
        if m and m.group(2) == m.group(4):
            vals = [self.num(f"{c}{m.group(2)}") for c in _cols(m.group(1), m.group(3))]
            vals = [v for v in vals if v is not None]
            out = 1.0
            for v in vals:
                out *= v
            return out if vals else None
        m = re.fullmatch(r"=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)", f)
        if m and m.group(1) == m.group(3):
            vals = [self.num(f"{m.group(1)}{r}") for r in range(int(m.group(2)), int(m.group(4)) + 1)]
            return sum(v for v in vals if v is not None)
        return None        # grand totals and other complex formulas are not needed


def _cols(a: str, b: str):
    return [chr(c) for c in range(ord(a), ord(b) + 1)]


def _d(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return logic.parse_date(v)


def import_legacy_purchases(file, db: dict) -> tuple[list[dict], dict]:
    """Parse the 'Annual Kits & consumables Purchasing' workbook (one sheet per year).

    Returns (invoices_without_ids, report). Suppliers missing from db are returned in
    report['new_suppliers'] and must be inserted first (invoices carry supplier_name).
    """
    import openpyxl

    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(file)
    wb = openpyxl.load_workbook(file, data_only=False)
    items = db.get("items", [])
    invoices: list[dict] = []
    new_suppliers: dict[str, str] = {}
    report = {"sheets": [], "rows": 0, "unmatched_items": set(), "date_fixes": []}

    def sup_id(name: str) -> tuple[str, str]:
        s = logic.supplier_by_name(db, name)
        if s:
            return s["id"], s["name"]
        key = logic.norm_key(name)
        for k, v in new_suppliers.items():
            if logic.norm_key(k) == key:
                return "", k
        pretty = name.strip().title()
        new_suppliers[pretty] = pretty
        return "", pretty

    for ws in wb.worksheets:
        ev = _Evaluator(ws)
        sheet_year = int(ws.title) if ws.title.strip().isdigit() else None
        report["sheets"].append(ws.title)
        cur = None
        closed = False
        last_date = None
        prev_desc = ""

        def fix_date(d: date | None, row: int) -> tuple[date | None, str]:
            if d and sheet_year and d.year == sheet_year - 1 and d.month <= 3:
                nd = d.replace(year=sheet_year)
                report["date_fixes"].append(f"{ws.title}!A{row}: {d} → {nd}")
                return nd, f"Date in file {d.strftime('%d/%m/%Y')} was in the {sheet_year} sheet – read as {nd.strftime('%d/%m/%Y')}"
            return d, ""

        def new_invoice(d: date | None, supplier: str, row: int, why: list[str]):
            sid, sname = sup_id(supplier) if supplier else ("", "(unknown)")
            inv = {"date": (d or last_date or date(sheet_year or 2025, 1, 1)).isoformat(),
                   "supplier_id": sid, "supplier_name": sname, "branch": "Diamond",
                   "invoice_no": "", "po_id": "", "lines": [], "delivery_fee": 0.0,
                   "discount": 0.0, "payments": [], "notes": "",
                   "needs_review": False, "review_notes": [], "source": "import",
                   "import_key": hashlib.md5(f"{ws.title}:{row}".encode()).hexdigest()[:12],
                   "import_ref": f"{ws.title}!row {row}"}
            for w in why:
                if w:
                    inv["review_notes"].append(w)
            invoices.append(inv)
            return inv

        for r in range(3, ws.max_row + 1):
            A = _d(ws[f"A{r}"].value)
            B = logic.clean_str(ws[f"B{r}"].value)
            C = logic.clean_str(ws[f"C{r}"].value)
            D = logic.clean_str(ws[f"D{r}"].value)
            E = logic.to_float(ws[f"E{r}"].value)
            F = logic.to_float(ws[f"F{r}"].value)
            G = ev.num(f"G{r}")
            H = logic.clean_str(ws[f"H{r}"].value)
            I = logic.clean_str(ws[f"I{r}"].value)
            if not any([A, B, C, D, E, F, G is not None, H, I]):
                continue
            report["rows"] += 1
            texts = " ".join([H, I])
            if "اجمالي" in texts or "إجمالي" in texts:
                continue
            fix_note = ""
            if A:
                A, fix_note = fix_date(A, r)
            meth = method_of(H)
            notes = " ".join(t for t in (H if not meth else "", I) if t)

            # ---------------- payment / subtotal rows (no item)
            is_gift_row = not C and (E is not None or F is not None) and H.lower() in ("gift", "free")
            if not C and not is_gift_row:
                if "الباقي" in texts:
                    continue
                if G is None:
                    if cur and notes:
                        cur["notes"] = (cur["notes"] + " | " + notes).strip(" |")
                    continue
                if A is not None:                     # dated amount → payment
                    if cur is None:
                        continue
                    pay = {"date": A.isoformat(), "amount": round(abs(G), 2),
                           "method": meth or "Other", "ref": "", "notes": notes}
                    if not meth:
                        pay["notes"] = (pay["notes"] + " method not in file").strip()
                        cur["review_notes"].append("Payment method not written in the file")
                    if fix_note:
                        cur["review_notes"].append(fix_note)
                    cur["payments"].append(pay)
                    closed = True
                    last_date = A
                    continue
                if meth and cur is not None:          # payment without a date
                    cur["payments"].append({"date": cur["date"], "amount": round(abs(G), 2),
                                            "method": meth, "ref": "",
                                            "notes": "payment date not in file"})
                    cur["review_notes"].append("Payment date not in file – invoice date used")
                    closed = True
                    continue
                if cur and notes:                     # subtotal row with a remark
                    cur["notes"] = (cur["notes"] + " | " + notes).strip(" |")
                continue

            # ---------------- item rows
            if A and B and cur and not closed and cur["date"] == A.isoformat() \
                    and sup_id(B)[1] == cur["supplier_name"]:
                pass                                   # same invoice, date repeated on each row
            elif A and B:
                cur = new_invoice(A, B, r, [fix_note])
                closed = False
            elif A and not B:
                sup = cur["supplier_name"] if cur else ""
                cur = new_invoice(A, sup, r, [fix_note, "Supplier not written – same as previous row"])
                closed = False
            elif B and (cur is None or closed or sup_id(B)[1] != cur["supplier_name"]):
                cur = new_invoice(last_date, B, r, ["Invoice date not in file – previous date used"])
                closed = False
            elif cur is None:
                cur = new_invoice(last_date, "", r, ["Supplier and date not in file"])
            if A:
                last_date = A

            desc = C or prev_desc
            prev_desc = desc
            if _is_delivery(desc):
                fee = G if G is not None else (E or 1) * (F or 0)
                cur["delivery_fee"] = round(cur["delivery_fee"] + (fee or 0), 2)
                if "الدفع" in texts or "نم الدفع" in texts or "تم الدفع" in texts:
                    cur["payments"].append({"date": cur["date"], "amount": round(fee or 0, 2),
                                            "method": "Cash", "ref": "",
                                            "notes": "delivery fee paid on delivery"})
                continue

            kind = "Purchase"
            hl = H.lower()
            if hl == "free":
                kind = "Free"
            elif hl == "gift":
                kind = "Gift"
            elif G is not None and G < 0:
                kind = "Return"
            qty, price, disc = E, F, 0.0
            line_notes = notes
            if kind == "Return":
                qty = qty or 1.0
                if G is not None and (price is None or abs(qty * price - abs(G)) > 0.01):
                    price = abs(G) / qty
            elif kind == "Purchase":
                if qty is None and price is None and G is not None:
                    qty, price = 1.0, G
                elif qty is not None and price is not None and G is not None and abs(G - qty * price) > 0.01:
                    if G == 0:
                        kind = "Free"
                    elif G > 0 and price and abs(G / price - round(G / price)) < 1e-6:
                        line_notes = (line_notes + f" (qty in file {logic.fmt_num(qty)})").strip()
                        qty = round(G / price, 3)
                    elif 0 < G < qty * price:
                        disc = round((1 - G / (qty * price)) * 100, 2)
                    else:
                        price = G / qty if qty else G
                elif qty is None and price is not None:
                    qty = 1.0
            iid = logic.match_item(desc, items) or ""
            if not iid:
                report["unmatched_items"].add(desc)
            it = next((x for x in items if x["id"] == iid), {})
            cur["lines"].append({
                "item_id": iid, "description": desc,
                "category": category_of(D) or config.TYPE_TO_CATEGORY.get(it.get("type", ""), ""),
                "brand": "", "kit": "", "lot": "", "exp": "",
                "qty": qty if qty is not None else 0.0,
                "unit_price": round(price, 4) if price is not None else 0.0,
                "discount_pct": disc, "kind": kind, "notes": line_notes,
            })
            if meth:                                   # paid on the same line
                cur["payments"].append({"date": (A or _d(cur["date"])).isoformat(),
                                        "amount": round(logic.line_total(cur["lines"][-1]), 2),
                                        "method": meth, "ref": "", "notes": ""})

    # ---------------- tidy up: review flags + credit allocation for stand-alone returns
    for inv in invoices:
        t = logic.invoice_totals(inv)
        if not inv["payments"] and t["total"] > 0:
            inv["review_notes"].append("No payment recorded in the file for this invoice")
        elif inv["payments"] and 0 < abs(t["balance"]) <= 50:
            inv["review_notes"].append(
                f"Paid amount differs from the invoice total by {logic.fmt_num(t['balance'])} "
                "(rounding / discount?)")
    _allocate_credits(invoices)
    for inv in invoices:
        inv["review_notes"] = list(dict.fromkeys(inv["review_notes"]))
        inv["needs_review"] = bool(inv["review_notes"])
    report["unmatched_items"] = sorted(report["unmatched_items"])
    report["new_suppliers"] = sorted(new_suppliers)
    report["invoices"] = len(invoices)
    return invoices, report


def link_suppliers(db: dict, invoices: list[dict]) -> None:
    """After new suppliers were saved, fill supplier_id from supplier_name."""
    for inv in invoices:
        if not inv.get("supplier_id"):
            s = logic.supplier_by_name(db, inv.get("supplier_name", ""))
            if s:
                inv["supplier_id"] = s["id"]
                inv["supplier_name"] = s["name"]


def _allocate_credits(invoices: list[dict]) -> None:
    """A return booked alone, then deducted from the next payment to the same supplier."""
    for x in invoices:
        tx = logic.invoice_totals(x)
        if tx["balance"] >= 0 or x["payments"]:
            continue
        credit = -tx["balance"]
        for y in sorted(invoices, key=lambda i: i["date"]):
            if y is x or y["supplier_name"] != x["supplier_name"] or y["date"] < x["date"]:
                continue
            ty = logic.invoice_totals(y)
            if ty["balance"] > 0 and abs(ty["balance"] - credit) < 1:
                ref = f"Return {logic.fmt_date(x['date'])}"
                y["payments"].append({"date": y["payments"][-1]["date"] if y["payments"] else y["date"],
                                      "amount": round(credit, 2), "method": "Credit note",
                                      "ref": ref, "notes": "return deducted from this payment"})
                x["payments"].append({"date": x["date"], "amount": round(-credit, 2),
                                      "method": "Credit note", "ref": f"Used on invoice {logic.fmt_date(y['date'])}",
                                      "notes": "credit used"})
                break


# =============================================================== inventory template
TEMPLATE_COLS = ["No", "Item", "Branch", "Brand", "Active Qty", "Active Unit", "Active Exp",
                 "Stock Qty", "Stock Unit", "Stock Exp", "Status", "Remark"]


def import_inventory_excel(file, db: dict) -> tuple[list[dict], list[str]]:
    import openpyxl

    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(file)
    wb = openpyxl.load_workbook(file, data_only=True)
    ws = wb.worksheets[0]
    header_row, cols = None, {}
    for r in range(1, min(ws.max_row, 15) + 1):
        vals = [logic.clean_str(c.value) for c in ws[r]]
        if "Item" in vals:
            header_row = r
            cols = {v: i for i, v in enumerate(vals) if v}
            break
    if header_row is None:
        return [], ["Header row with an 'Item' column was not found."]
    items = db.get("items", [])
    by_name = {logic.norm_key(i["name"]): i for i in items}
    lines, errors = [], []
    for r in range(header_row + 1, ws.max_row + 1):
        row = [c.value for c in ws[r]]

        def g(col):
            i = cols.get(col)
            return row[i] if i is not None and i < len(row) else None

        name = logic.clean_str(g("Item"))
        if not name:
            continue
        it = by_name.get(logic.norm_key(name))
        if not it:
            iid = logic.match_item(name, items)
            it = next((x for x in items if x["id"] == iid), None)
        if not it:
            errors.append(f"Row {r}: item '{name}' is not in the catalog (add it first).")
            continue
        ln = {"item_id": it["id"], "branch": logic.clean_str(g("Branch")) or "D",
              "brand": logic.clean_str(g("Brand")),
              "active_qty": logic.to_float(g("Active Qty")),
              "active_unit": logic.clean_str(g("Active Unit")),
              "stock_qty": logic.to_float(g("Stock Qty")),
              "stock_unit": logic.clean_str(g("Stock Unit")),
              "status": logic.clean_str(g("Status")) or "Auto",
              "remark": logic.clean_str(g("Remark"))}
        for k, col in (("active_exp", "Active Exp"), ("stock_exp", "Stock Exp")):
            try:
                ln[k] = logic.parse_exp(g(col)) or ""
            except ValueError as e:
                errors.append(f"Row {r} ({name}): {e}")
                ln[k] = ""
        if ln["status"] not in config.STATUS_CHOICES:
            ln["status"] = "Auto"
        if ln["branch"] not in config.LINE_BRANCHES:
            ln["branch"] = config.BRANCH_CODE.get(ln["branch"], "D")
        lines.append(ln)
    return lines, errors
