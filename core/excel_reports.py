"""Excel outputs (openpyxl): purchases register (like the annual purchasing sheet),
inventory count, entry template, supplier statement, expiry list and full backup."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import config, logic

ORANGE = "E46C0A"
HDR_FILL = PatternFill("solid", fgColor=ORANGE)
HDR_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FILL = PatternFill("solid", fgColor="F3F3F3")
THIN = Side(style="thin", color="A0A0A0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = '#,##0.00;[Red]-#,##0.00'
WRAP = Alignment(wrap_text=True, vertical="center")


def _header(ws, row: int, cols: list[str], widths: list[float] | None = None):
    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=row, column=i, value=c)
        cell.fill, cell.font, cell.border = HDR_FILL, HDR_FONT, BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[row].height = 30


def _title(ws, text: str, sub: str, ncols: int):
    ws.cell(row=1, column=1, value=text).font = Font(bold=True, size=14)
    ws.cell(row=2, column=1, value=sub).font = Font(size=9, color="666666")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)


def _table(ws, start_row: int, cols: list[str], rows: list[list], widths=None, money_cols=()):
    _header(ws, start_row, cols, widths)
    for r_i, row in enumerate(rows, start_row + 1):
        for c_i, v in enumerate(row, 1):
            cell = ws.cell(row=r_i, column=c_i, value=v)
            cell.border = BORDER
            cell.alignment = WRAP
            if cols[c_i - 1] in money_cols:
                cell.number_format = MONEY
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(cols))}{start_row + len(rows)}"


def _bytes(wb: Workbook) -> bytes:
    for ws in wb.worksheets:                 # print: landscape, fit all columns on one page width
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =============================================================== purchases register
REG_COLS = ["Date", "Supplier Name", "Item Description", "Category", "Brand", "Kit / Pack",
            "Lot No.", "Exp. Date", "Quantity", "Unit Price", "Disc %", "Total Cost",
            "Payment Method", "Payment Date", "Notes"]
REG_WIDTHS = [11, 16, 28, 12, 12, 11, 10, 9, 9, 11, 7, 13, 18, 13, 34]


def register_xlsx(db: dict, start: date | None, end: date | None, supplier_id: str | None = None) -> bytes:
    invs = logic.invoices_in(db, start, end, supplier_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Register"
    period = f"{logic.fmt_date(start) or '…'} → {logic.fmt_date(end) or '…'}"
    _title(ws, "Annual Kits & Consumables Purchasing",
           f"{period} · {logic.supplier_name(db, supplier_id) if supplier_id else 'All suppliers'} · "
           f"{db['settings'].get('currency', 'EGP')} · generated {logic.fmt_date(date.today())}", len(REG_COLS))
    _header(ws, 4, REG_COLS, REG_WIDTHS)
    r = 5
    total_cells = []
    red = Font(color="E00000")
    for inv in invs:
        t = logic.invoice_totals(inv)
        sname = inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id"))
        first_row = r
        for i, ln in enumerate(inv.get("lines", [])):
            kind = logic.clean_str(ln.get("kind")) or "Purchase"
            d = logic.parse_date(inv.get("date"))
            vals = [d if i == 0 else None, sname if i == 0 else None, ln.get("description", ""),
                    ln.get("category", ""), ln.get("brand", ""), ln.get("kit", ""), ln.get("lot", ""),
                    logic.fmt_exp(ln.get("exp")), logic.to_float(ln.get("qty")),
                    logic.to_float(ln.get("unit_price")), logic.to_float(ln.get("discount_pct")) or None]
            for c, v in enumerate(vals, 1):
                ws.cell(row=r, column=c, value=v).border = BORDER
            if kind == "Purchase":
                f = f"=ROUND(I{r}*J{r}*(1-N(K{r})/100),2)"
            elif kind == "Return":
                f = f"=-ROUND(I{r}*J{r}*(1-N(K{r})/100),2)"
            else:
                f = 0
            ws.cell(row=r, column=12, value=f).number_format = MONEY
            note = logic.clean_str(ln.get("notes"))
            if kind != "Purchase":
                note = kind + (f" – {note}" if note else "")
            ws.cell(row=r, column=15, value=note)
            for c in range(12, 16):
                ws.cell(row=r, column=c).border = BORDER
            ws.cell(row=r, column=1).number_format = "dd/mm/yyyy"
            ws.cell(row=r, column=10).number_format = MONEY
            if kind == "Return":
                for c in range(1, 16):
                    ws.cell(row=r, column=c).font = red
            r += 1
        if t["delivery"]:
            ws.cell(row=r, column=3, value="Delivery fee")
            ws.cell(row=r, column=9, value=1)
            ws.cell(row=r, column=10, value=t["delivery"]).number_format = MONEY
            ws.cell(row=r, column=12, value=f"=ROUND(I{r}*J{r},2)").number_format = MONEY
            for c in range(1, 16):
                ws.cell(row=r, column=c).border = BORDER
            r += 1
        if t["discount"]:
            ws.cell(row=r, column=3, value="Invoice discount")
            ws.cell(row=r, column=12, value=-t["discount"]).number_format = MONEY
            for c in range(1, 16):
                ws.cell(row=r, column=c).border = BORDER
            r += 1
        pays = inv.get("payments", [])
        ws.cell(row=r, column=3, value=f"Invoice total · {inv['id']}" +
                (f" · No. {inv['invoice_no']}" if inv.get("invoice_no") else ""))
        ws.cell(row=r, column=12, value=f"=SUM(L{first_row}:L{r - 1})" if r > first_row else 0)
        ws.cell(row=r, column=12).number_format = MONEY
        ws.cell(row=r, column=13, value="\n".join(
            f"{logic.fmt_num(p.get('amount'))} {p.get('method', '')}" for p in pays) or "—")
        ws.cell(row=r, column=14, value="\n".join(logic.fmt_date(p.get("date")) for p in pays) or "—")
        status = f"Paid {logic.fmt_num(t['paid'])} · Balance {logic.fmt_num(t['balance'])} · {t['status']}"
        extra = [logic.clean_str(inv.get("notes"))] + (inv.get("review_notes") or [] if inv.get("needs_review") else [])
        ws.cell(row=r, column=15, value="\n".join([status] + [e for e in extra if e]))
        for c in range(1, 16):
            cell = ws.cell(row=r, column=c)
            cell.fill, cell.border = TOTAL_FILL, BORDER
            cell.font = Font(bold=c in (3, 12))
            cell.alignment = WRAP
        total_cells.append(f"L{r}")
        r += 2
    ws.cell(row=r, column=3, value="Total purchases (net of returns)").font = Font(bold=True)
    ws.cell(row=r, column=12, value=f"=SUM({','.join(total_cells)})" if total_cells else 0)
    ws.cell(row=r, column=12).number_format = MONEY
    ws.cell(row=r, column=12).font = Font(bold=True)
    paid = sum(logic.invoice_totals(i)["paid"] for i in invs)
    ws.cell(row=r + 1, column=3, value="Total paid").font = Font(bold=True)
    ws.cell(row=r + 1, column=12, value=paid).number_format = MONEY
    ws.cell(row=r + 2, column=3, value="Balance due").font = Font(bold=True)
    ws.cell(row=r + 2, column=12, value=f"=L{r}-L{r + 1}").number_format = MONEY
    ws.freeze_panes = "A5"

    # flat lines
    ws2 = wb.create_sheet("Lines")
    flat = logic.purchase_lines_flat(db, start, end, supplier_id)
    cols = list(flat[0].keys()) if flat else ["Invoice"]
    _table(ws2, 1, cols, [[r_[c] for c in cols] for r_ in flat],
           [12, 11, 16, 9, 26, 18, 11, 11, 10, 9, 8, 9, 8, 9, 9, 7, 11, 11, 14, 10, 24],
           money_cols=("Unit price", "Line total"))

    # payments
    ws3 = wb.create_sheet("Payments")
    prow = []
    for inv in invs:
        for p in inv.get("payments", []):
            prow.append([logic.parse_date(p.get("date")),
                         inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id")),
                         inv["id"], logic.to_float(p.get("amount")), p.get("method", ""), p.get("ref", ""),
                         p.get("notes", "")])
    _table(ws3, 1, ["Date", "Supplier", "Invoice", "Amount", "Method", "Ref", "Notes"], prow,
           [12, 20, 15, 13, 16, 22, 34], money_cols=("Amount",))
    for row in ws3.iter_rows(min_row=2, max_col=1):
        row[0].number_format = "dd/mm/yyyy"

    # by supplier (period)
    ws4 = wb.create_sheet("By supplier")
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    for inv in invs:
        t = logic.invoice_totals(inv)
        k = inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id"))
        a = agg[k]
        a[0] += 1
        a[1] += t["total"]
        a[2] += t["paid"]
        a[3] += t["balance"]
    _table(ws4, 1, ["Supplier", "Invoices", "Purchases", "Paid", "Balance"],
           [[k, *v] for k, v in sorted(agg.items(), key=lambda kv: -kv[1][1])], [24, 10, 14, 14, 14],
           money_cols=("Purchases", "Paid", "Balance"))

    ws5 = wb.create_sheet("By month")
    months = defaultdict(float)
    for inv in invs:
        months[(inv.get("date") or "")[:7]] += logic.invoice_totals(inv)["total"]
    _table(ws5, 1, ["Month", "Spend"], [[k, round(v, 2)] for k, v in sorted(months.items())], [12, 14],
           money_cols=("Spend",))

    ws6 = wb.create_sheet("By category")
    cat = defaultdict(float)
    for f in flat:
        cat[f["Category"] or "(none)"] += f["Line total"]
    _table(ws6, 1, ["Category", "Spend"], [[k, round(v, 2)] for k, v in sorted(cat.items(), key=lambda kv: -kv[1])],
           [16, 14], money_cols=("Spend",))
    return _bytes(wb)


# =============================================================== inventory count
def count_xlsx(session: dict, db: dict) -> bytes:
    items = logic.index(db, "items")
    ref = logic.parse_date(session.get("date")) or date.today()
    warn = int(db["settings"].get("expiry_warn_days", 60))
    wb = Workbook()
    ws = wb.active
    ws.title = session.get("group", "Count")[:30]
    cols = ["No", "Item", "Type", "Branch", "Comp. (brand)", "Active Qty", "Active Unit", "Active Exp",
            "Stock Qty", "Stock Unit", "Stock Exp", "Status", "App suggests", "Availability", "Remark"]
    _title(ws, f"Inventory / Branches / Instruments / Stock / Balance ({session.get('group', '')})",
           f"Branch: {config.BRANCH_LABEL.get(session.get('branch'), session.get('branch'))} · "
           f"Date: {logic.fmt_date(session.get('date'))} · {session.get('id', '')} · counted by "
           f"{session.get('counted_by', '')}", len(cols))
    _header(ws, 4, cols, [5, 24, 11, 8, 14, 10, 10, 10, 10, 10, 10, 8, 11, 14, 30])
    per = defaultdict(list)
    for ln in session.get("lines", []):
        per[(ln.get("item_id"), logic.clean_str(ln.get("branch")))].append(ln)
    evals = {k: logic.evaluate_lines(v, items.get(k[0]), ref, warn) for k, v in per.items()}
    lines = sorted(session.get("lines", []), key=lambda l: (items.get(l.get("item_id"), {}).get("order", 9999),
                                                             l.get("branch", "")))
    r, n, last = 5, 0, None
    for ln in lines:
        it = items.get(ln.get("item_id"), {})
        key = (ln.get("item_id"), logic.clean_str(ln.get("branch")))
        ev = evals[key]
        if ln.get("item_id") != last:
            n += 1
            last = ln.get("item_id")
        vals = [n, it.get("name", ""), it.get("type", ""), ln.get("branch", ""), ln.get("brand", ""),
                logic.to_float(ln.get("active_qty")), ln.get("active_unit", ""), logic.fmt_exp(ln.get("active_exp")),
                logic.to_float(ln.get("stock_qty")), ln.get("stock_unit", ""), logic.fmt_exp(ln.get("stock_exp")),
                ev["status"], ev["auto_status"], ev["avail"], ln.get("remark", "")]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = BORDER
        ws.cell(row=r, column=2).font = Font(color=config.AVAIL_COLORS[ev["avail"]].lstrip("#"))
        for c, k in ((8, "active_exp"), (11, "stock_exp")):
            e = logic.exp_end(ln.get(k))
            if e and e < ref:
                ws.cell(row=r, column=c).font = Font(color="E00000", bold=True)
        ws.cell(row=r, column=12).font = Font(bold=True)
        r += 1
    ws.cell(row=r + 1, column=2, value="Available ► Black · Expired ► Red · Small amount ► Blue · "
                                       "Not available ► Green · Status: G = Good, B = Need to buy")
    ws.freeze_panes = "C5"
    return _bytes(wb)


# =============================================================== entry template
def template_xlsx(db: dict, group: str, branch: str, copy_last: bool = True) -> bytes:
    from .importers import TEMPLATE_COLS
    items = logic.index(db, "items")
    lines = logic.lines_for_new_count(db, group, branch, copy_last)
    wb = Workbook()
    ws = wb.active
    ws.title = "Count"
    ws.cell(row=1, column=1, value=f"Orange Lab inventory count – {group} – branch: "
                                   f"{config.BRANCH_LABEL.get(branch, branch)}").font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value="Fill quantities and expiry as MM/YYYY (e.g. 11/2026). Add a row for "
                                   "each extra brand/lot. Status: Auto / G / B / -. Then upload it in the "
                                   "app (Inventory count → Upload Excel).").font = Font(size=9, color="666666")
    _header(ws, 4, TEMPLATE_COLS, [5, 26, 8, 14, 10, 10, 10, 10, 10, 10, 8, 30])
    for i, ln in enumerate(lines, 1):
        it = items.get(ln["item_id"], {})
        vals = [i, it.get("name", ""), ln.get("branch", ""), ln.get("brand", ""), ln.get("active_qty"),
                ln.get("active_unit", ""), logic.fmt_exp(ln.get("active_exp")), ln.get("stock_qty"),
                ln.get("stock_unit", ""), logic.fmt_exp(ln.get("stock_exp")), ln.get("status") or "Auto",
                ln.get("remark", "")]
        for c, v in enumerate(vals, 1):
            ws.cell(row=4 + i, column=c, value=v).border = BORDER
        for c in (7, 10):
            ws.cell(row=4 + i, column=c).number_format = "@"
    last = 4 + max(len(lines), 1) + 60
    dv_b = DataValidation(type="list", formula1='"D,L,D&L"', allow_blank=True)
    dv_s = DataValidation(type="list", formula1='"Auto,G,B,-"', allow_blank=True)
    dv_u = DataValidation(type="list", formula1='"' + ",".join(config.UNITS) + '"', allow_blank=True)
    for dv in (dv_b, dv_s, dv_u):
        ws.add_data_validation(dv)
    dv_b.add(f"C5:C{last}")
    dv_s.add(f"K5:K{last}")
    dv_u.add(f"F5:F{last}")
    dv_u.add(f"I5:I{last}")
    ws.freeze_panes = "C5"
    ws2 = wb.create_sheet("Catalog")
    _table(ws2, 1, ["Item", "Group", "Type", "Unit", "Branches"],
           [[i["name"], i["group"], i["type"], i.get("unit", ""), i.get("branches", "")]
            for i in logic.sorted_items(db)], [28, 12, 16, 10, 10])
    return _bytes(wb)


# =============================================================== statement / expiry / backup
def statement_xlsx(db: dict, supplier_id: str, start: date | None, end: date | None) -> bytes:
    rows, opening = logic.supplier_statement(db, supplier_id, start, end)
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    _title(ws, f"Supplier statement – {logic.supplier_name(db, supplier_id)}",
           f"{logic.fmt_date(start) or 'start'} → {logic.fmt_date(end) or 'today'}", 7)
    data = [[logic.parse_date(start) if start else None, "Opening", "", "Opening balance", None, None, opening]]
    for r in rows:
        data.append([logic.parse_date(r["date"]), r["type"], r["ref"], r["desc"], r["debit"] or None,
                     r["credit"] or None, r["balance"]])
    _table(ws, 4, ["Date", "Type", "Ref", "Description", "Debit", "Credit", "Balance"], data,
           [12, 10, 16, 50, 13, 13, 13], money_cols=("Debit", "Credit", "Balance"))
    for row in ws.iter_rows(min_row=5, max_col=1):
        row[0].number_format = "dd/mm/yyyy"
    return _bytes(wb)


def expiry_xlsx(db: dict, days: int) -> bytes:
    data = logic.expiry_alerts(db, days)
    wb = Workbook()
    ws = wb.active
    ws.title = "Expiry"
    cols = ["Branch", "Item", "Group", "Brand", "Place", "Qty", "Exp", "Days left", "State", "Count"]
    _title(ws, "Expiry report", f"Expired and expiring within {days} days · {logic.fmt_date(date.today())}",
           len(cols))
    _table(ws, 4, cols, [[r[c] for c in cols] for r in data], [8, 24, 11, 14, 8, 12, 9, 9, 14, 14])
    return _bytes(wb)


def backup_xlsx(db: dict) -> bytes:
    items = logic.index(db, "items")
    wb = Workbook()
    ws = wb.active
    ws.title = "Items"
    icols = ["id", "name", "group", "type", "unit", "purchase_unit", "default_brand", "default_supplier_id",
             "branches", "min_qty", "reorder_qty", "aliases", "active", "notes"]
    _table(ws, 1, icols, [[i.get(c) for c in icols] for i in logic.sorted_items(db, active_only=False)])
    ws = wb.create_sheet("Suppliers")
    scols = ["id", "name", "phone", "whatsapp", "email", "contact", "address", "supplies", "payment_terms",
             "notes", "aliases", "active"]
    _table(ws, 1, scols, [[s.get(c) for c in scols] for s in db.get("suppliers", [])])
    ws = wb.create_sheet("Count lines")
    lcols = ["count_id", "date", "group", "session_branch", "item", "branch", "brand", "active_qty",
             "active_unit", "active_exp", "stock_qty", "stock_unit", "stock_exp", "status", "remark"]
    rows = []
    for c in db.get("counts", []):
        for ln in c.get("lines", []):
            rows.append([c["id"], c.get("date"), c.get("group"), c.get("branch"),
                         items.get(ln.get("item_id"), {}).get("name", ln.get("item_id")), ln.get("branch"),
                         ln.get("brand"), ln.get("active_qty"), ln.get("active_unit"), ln.get("active_exp"),
                         ln.get("stock_qty"), ln.get("stock_unit"), ln.get("stock_exp"), ln.get("status"),
                         ln.get("remark")])
    _table(ws, 1, lcols, rows)
    ws = wb.create_sheet("PO lines")
    rows = []
    for po in db.get("purchase_orders", []):
        for ln in po.get("lines", []):
            rows.append([po["id"], po.get("date"), logic.supplier_name(db, po.get("supplier_id")), po.get("status"),
                         ln.get("name"), ln.get("brand"), ln.get("qty"), ln.get("unit"), ln.get("received_qty"),
                         ln.get("branch"), ln.get("notes")])
    _table(ws, 1, ["po", "date", "supplier", "status", "item", "brand", "qty", "unit", "received", "branch",
                   "notes"], rows)
    ws = wb.create_sheet("Invoice lines")
    flat = logic.purchase_lines_flat(db)
    cols = list(flat[0].keys()) if flat else ["Invoice"]
    _table(ws, 1, cols, [[r[c] for c in cols] for r in flat])
    ws = wb.create_sheet("Payments")
    rows = [[inv["id"], inv.get("supplier_name"), p.get("date"), p.get("amount"), p.get("method"), p.get("ref"),
             p.get("notes")] for inv in db.get("invoices", []) for p in inv.get("payments", [])]
    _table(ws, 1, ["invoice", "supplier", "date", "amount", "method", "ref", "notes"], rows)
    return _bytes(wb)
