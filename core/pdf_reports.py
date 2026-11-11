"""PDF outputs (reportlab): purchase order, inventory count, purchases register,
supplier statement and expiry report. Layouts follow Orange Lab's paper forms."""
from __future__ import annotations

import io
import os
from collections import defaultdict
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from . import config, logic
from .arabic import ar, has_arabic

ORANGE = colors.HexColor("#E46C0A")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#F3F3F3")
RED = colors.HexColor(config.AVAIL_COLORS[config.AVAIL_EXPIRED])

_FONTS_OK = False


def _fonts():
    global _FONTS_OK
    if _FONTS_OK:
        return
    for name, f in (("DV", "DejaVuSans.ttf"), ("DVB", "DejaVuSans-Bold.ttf"),
                    ("DVC", "DejaVuSansCondensed.ttf"), ("DVCB", "DejaVuSansCondensed-Bold.ttf")):
        pdfmetrics.registerFont(TTFont(name, os.path.join(config.FONTS_DIR, f)))
    pdfmetrics.registerFontFamily("DV", normal="DV", bold="DVB", italic="DV", boldItalic="DVB")
    pdfmetrics.registerFontFamily("DVC", normal="DVC", bold="DVCB", italic="DVC", boldItalic="DVCB")
    _FONTS_OK = True


def S(name="n", size=9, font="DV", align=TA_LEFT, color=colors.black, leading=None, bold=False):
    _fonts()
    return ParagraphStyle(name, fontName=(font + "B") if bold else font, fontSize=size,
                          leading=leading or size * 1.22, alignment=align, textColor=color)


def P(text, style) -> Paragraph:
    """Paragraph with Arabic shaping and XML escaping; '\n' becomes a line break."""
    s = "" if text is None else str(text)
    parts = []
    for line in s.split("\n"):
        parts.append(escape(ar(line)) if has_arabic(line) else escape(line))
    return Paragraph("<br/>".join(parts), style)


def Praw(markup, style) -> Paragraph:
    return Paragraph(markup, style)


def _logo(width_cm=4.2):
    if not os.path.exists(config.LOGO_PATH):
        return Spacer(1, 1)
    img = Image(config.LOGO_PATH)
    ratio = img.imageHeight / float(img.imageWidth)
    img.drawWidth = width_cm * cm
    img.drawHeight = width_cm * cm * ratio
    return img


def _footer_branches(canv, settings, page_w, y=1.15 * cm, margin=1.6 * cm):
    _fonts()
    canv.setStrokeColor(ORANGE)
    canv.setLineWidth(0.8)
    canv.line(margin, y + 0.95 * cm, page_w - margin, y + 0.95 * cm)
    canv.setFillColor(ORANGE)
    yy = y + 0.45 * cm
    for code in config.BRANCHES:
        b = settings.get("branches", {}).get(code, {})
        addr, phones = b.get("address_ar", ""), b.get("phones", "")
        canv.setFont("DVB", 8.6)
        canv.drawString(margin, yy, f"{phones}  / " + ar("ت"))
        canv.setFont("DV", 8.6)
        canv.drawRightString(page_w - margin, yy, ar(addr))
        yy -= 0.42 * cm
    canv.setFillColor(colors.black)


# =============================================================== purchase order
def po_pdf(po: dict, db: dict) -> bytes:
    _fonts()
    st = db["settings"]
    sup = logic.index(db, "suppliers").get(po.get("supplier_id") or "", {})
    buf = io.BytesIO()
    W, H = A4
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.0 * cm, bottomMargin=2.6 * cm,
                            title=f"Purchase order {po.get('id', '')}",
                            author=st.get("lab_name", "Orange Lab"))
    story = []
    ref = Praw(f"<font color='#666666'>Ref: <b>{escape(po.get('id', '') or 'DRAFT')}</b>"
               f"&nbsp;&nbsp;·&nbsp;&nbsp;{escape(po.get('status', 'Draft'))}</font>", S(size=8.5))
    head = Table([[ref, _logo(4.3)]], colWidths=[W - 3.2 * cm - 4.6 * cm, 4.6 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                              ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 0.25 * cm)]

    def box(on):
        return "☑" if on else "☐"

    ch = po.get("channels", [])
    chan = Table([[P(f"{box('Email' in ch)}Email", S(size=12, bold=True)),
                   P(box("What's app" in ch) + "What’s app", S(size=12, bold=True, align=TA_CENTER)),
                   P(f"{box('Print' in ch)}Print", S(size=12, bold=True, align=TA_RIGHT))]],
                 colWidths=[(W - 3.2 * cm) / 3] * 3)
    chan.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [chan, Spacer(1, 0.3 * cm)]

    from_txt = st.get("po_from", "")
    if po.get("branch") in config.BRANCHES:
        from_txt += f" ({config.BRANCH_LABEL[po['branch']]})"
    lab = S(size=9, bold=True)
    val = S(size=10)
    hdr = Table([
        [P("T0", lab), P(sup.get("name", ""), S(size=10.5, bold=True)), P("From:", lab), P(from_txt, val)],
        [P("Phone:", lab), P(sup.get("phone", ""), val), P("Mob :", lab), P(st.get("po_mobile", ""), val)],
        [P("Page", lab), P("1", val), P("Date:", lab), P(logic.fmt_date(po.get("date")), val)],
    ], colWidths=[2.6 * cm, 5.8 * cm, 2.6 * cm, 6.8 * cm], rowHeights=[0.62 * cm] * 3)
    hdr.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                             ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [hdr, Spacer(1, 0.3 * cm)]

    fl = po.get("flags", [])
    flags = Table([[P(f"{box(f in fl)} {f}", S(size=9, bold=True)) for f in config.PO_FLAGS]],
                  colWidths=[2.5 * cm, 3.1 * cm, 4.0 * cm, 3.6 * cm, 4.0 * cm])
    flags.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 1)]))
    story += [flags, Spacer(1, 0.15 * cm)]
    line = Table([[""]], colWidths=[W - 3.2 * cm], rowHeights=[0.05 * cm])
    line.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, colors.black)]))
    story += [line, Spacer(1, 0.25 * cm)]

    hstyle = S(size=10, bold=True, align=TA_CENTER)
    rows = [[P("No", hstyle), P("Required", hstyle), P("Comp.", hstyle), P("Quantity", hstyle),
             P("Notes", hstyle)]]
    cstyle = S(size=10, align=TA_CENTER)
    bstyle = S(size=10, align=TA_CENTER, bold=True)
    nstyle = S(size=9.5, align=TA_CENTER)
    lines = po.get("lines", [])
    n_rows = max(len(lines), int(st.get("po_blank_rows", 23) or 0))
    for i in range(n_rows):
        if i < len(lines):
            ln = lines[i]
            qty = f"{logic.fmt_qty(ln.get('qty'))} {logic.clean_str(ln.get('unit'))}".strip()
            note = logic.clean_str(ln.get("notes"))
            if po.get("branch") == "All" and ln.get("branch"):
                note = (f"[{ln['branch']}] " + note).strip()
            rows.append([P(str(i + 1), S(size=9, bold=True, align=TA_CENTER)),
                         P(ln.get("name", ""), bstyle), P(ln.get("brand", ""), cstyle),
                         P(qty, bstyle), P(note, nstyle)])
        else:
            rows.append([P(str(i + 1), S(size=9, bold=True, align=TA_CENTER)), "", "", "", ""])
    t = Table(rows, colWidths=[1.3 * cm, 5.6 * cm, 3.3 * cm, 3.4 * cm, 4.2 * cm], repeatRows=1,
              rowHeights=[0.62 * cm] + [0.56 * cm] * n_rows)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    story.append(t)
    if logic.clean_str(po.get("notes")):
        story += [Spacer(1, 0.2 * cm), P("Notes: " + po["notes"], S(size=9.5))]
    story += [Spacer(1, 0.25 * cm), P("We appreciate your cooperation.", S(size=10.5, bold=True, align=TA_CENTER)),
              Spacer(1, 0.35 * cm)]
    left = [P(st.get("po_signature_left_title", "Inventory manager"), S(size=10)),
            P("Dr/ " + _strip_dr(po.get("signed_by") or ""), S(size=10))]
    right = [P(st.get("po_signature_right_title", "General manager"), S(size=10, align=TA_RIGHT)),
             P(st.get("general_manager", ""), S(size=10, align=TA_RIGHT)),
             P(st.get("coo", ""), S(size=10, align=TA_RIGHT))]
    sig = Table([[left, right]], colWidths=[(W - 3.2 * cm) / 2] * 2)
    sig.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([sig]))

    def on_page(canv, d):
        _footer_branches(canv, st, W)
        if d.page > 1:
            canv.setFont("DV", 8)
            canv.setFillColor(GREY)
            canv.drawString(1.6 * cm, H - 0.8 * cm, f"{po.get('id', '')} – page {d.page}")
            canv.setFillColor(colors.black)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def _strip_dr(name: str) -> str:
    n = logic.clean_str(name)
    for p in ("Dr/ ", "Dr/", "Dr. ", "Dr "):
        if n.startswith(p):
            return n[len(p):]
    return n


# =============================================================== inventory count
def _title_markup(group: str) -> str:
    def red(word):
        return f"<font color='#E00000'>{word[0]}</font>{word[1:]}"
    return (f"{red('Inventory')} / {red('Branches')}/{red('Instruments')}/{red('Stock')}/"
            f"{red('Balance')} <font name='DVB' size='13'>({group})</font>")


def count_pdf(session: dict, db: dict) -> bytes:
    _fonts()
    st = db["settings"]
    items = logic.index(db, "items")
    ref = logic.parse_date(session.get("date")) or date.today()
    warn = int(st.get("expiry_warn_days", 60))
    group = session.get("group", "Supplies")
    chem = group == "Chemicals"
    PW, PH = landscape(A4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=2.4 * cm, bottomMargin=2.0 * cm,
                            title=f"Inventory {group} {session.get('date', '')}")

    # one table row per item (all brands / branches stacked inside the row)
    per = defaultdict(list)
    for ln in session.get("lines", []):
        per[ln.get("item_id")].append(ln)
    order = sorted(per.keys(), key=lambda i: (items.get(i, {}).get("order", 9999),
                                               items.get(i, {}).get("name", i)))
    c9 = S(size=8.6, font="DVC", align=TA_CENTER, leading=10.5)
    c9l = S(size=8.6, font="DVC", align=TA_LEFT, leading=10.5)
    hdr = S(size=9, font="DV", bold=True, align=TA_CENTER, leading=10.5)

    def stack(vals, red_flags=None):
        out = []
        for i, v in enumerate(vals):
            v = "-" if v in ("", None) else str(v)
            v = escape(ar(v)) if has_arabic(v) else escape(v)
            if red_flags and red_flags[i]:
                v = f"<font color='#E00000'>{v}</font>"
            out.append(v)
        return "<br/>".join(out)

    if chem:
        head = ["NO", "Items", "Type", "Branch", "Comp.", "Active", "Exp. date", "Stock", "Exp.date",
                "Status", "Remark"]
        widths = [1.0, 3.6, 2.1, 1.7, 3.0, 3.0, 2.2, 3.0, 2.2, 1.6, 3.9]
    else:
        head = ["No", "Items", "Company", "Active", "Exp. date", "Stock", "Exp. date", "Status", "Remark"]
        widths = [1.0, 4.0, 3.2, 3.2, 2.4, 3.2, 2.4, 1.7, 6.2]
    rows = [[P(h, hdr) for h in head]]
    styles = [("GRID", (0, 0), (-1, -1), 0.6, colors.black),
              ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    for n, iid in enumerate(order, 1):
        lns = per[iid]
        it = items.get(iid, {"name": iid})
        ev = logic.evaluate_lines(lns, it, ref, warn)
        color = config.AVAIL_COLORS[ev["avail"]]
        name = f"<font color='{color}'>{escape(it.get('name', ''))}</font>"

        def qtxt(ln, place):
            q = logic.to_float(ln.get(f"{place}_qty"))
            if q is None:
                return ""
            return f"{logic.fmt_qty(q)} {logic.clean_str(ln.get(f'{place}_unit'))}".strip()

        def exp_flags(place):
            out = []
            for ln in lns:
                e = logic.exp_end(ln.get(f"{place}_exp"))
                out.append(bool(e and e < ref))
            return out

        brands = [logic.clean_str(l.get("brand")) for l in lns]
        a_q = [qtxt(l, "active") for l in lns]
        a_e = [logic.fmt_exp(l.get("active_exp")) for l in lns]
        s_q = [qtxt(l, "stock") for l in lns]
        s_e = [logic.fmt_exp(l.get("stock_exp")) for l in lns]
        remarks = [logic.clean_str(l.get("remark")) for l in lns if logic.clean_str(l.get("remark"))]
        rem = "<br/>".join(escape(ar(r)) if has_arabic(r) else escape(r) for r in remarks)
        status = ev["status"]
        stat = f"<b>{status}</b>"
        if ev["auto_status"] != status and status != "-":
            stat += f"<br/><font size='6.5' color='#2F6FD0'>app: {ev['auto_status']}</font>"
        row = [Praw(str(n), c9), Praw(name, S(size=9.2, align=TA_CENTER, leading=11))]
        if chem:
            row += [Praw(escape(it.get("type", "")), c9),
                    Praw(stack([l.get("branch", "") for l in lns]), c9)]
        row += [Praw(stack(brands), c9), Praw(stack(a_q), c9), Praw(stack(a_e, exp_flags("active")), c9),
                Praw(stack(s_q), c9), Praw(stack(s_e, exp_flags("stock")), c9),
                Praw(stat, S(size=9, align=TA_CENTER, leading=10)), Praw(rem, c9l)]
        rows.append(row)
    t = Table(rows, colWidths=[w * cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle(styles))
    legend = Praw(
        "<b>Available ►</b>Black&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>Expired ► <font color='#E00000'>Red</font></b>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>Small amount ► <font color='#2F6FD0'>Blue</font></b>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>Not available ►<font color='#00A04A'>Green</font></b>"
        "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;<b>Status (Good ► G _ Need to buy ► B)</b>"
        "&nbsp;&nbsp;&nbsp;<font size='7' color='#2F6FD0'>app: = status the app suggests</font>",
        S(size=9, align=TA_CENTER))
    summ = logic.count_summary(session, db)
    info = Praw(f"<font color='#666666'>{escape(session.get('id', ''))} · {summ['items']} items · "
                f"need to buy (B): {summ['B']} · with expired lots: {summ['expired']} · "
                f"not available: {summ['none']}</font>", S(size=8))
    story = [t, Spacer(1, 0.25 * cm), legend, Spacer(1, 0.15 * cm), info]
    if logic.clean_str(session.get("notes")):
        story += [Spacer(1, 0.1 * cm), P("Notes: " + session["notes"], S(size=8.5, color=GREY))]

    branch = config.BRANCH_LABEL.get(session.get("branch"), session.get("branch", ""))
    manager = session.get("counted_by") or st.get(
        "inventory_manager_chemicals" if chem else "inventory_manager_supplies", "")
    approver = session.get("approved_by") or st.get("coo", "")

    def on_page(canv, d):
        canv.saveState()
        para = Praw(_title_markup(group), S(size=15, align=TA_CENTER, bold=True, leading=18))
        w, h = para.wrap(PW - 2.4 * cm, 2 * cm)
        para.drawOn(canv, 1.2 * cm, PH - 1.25 * cm - h / 2)
        canv.setFont("DVB", 10)
        canv.drawString(1.6 * cm, PH - 2.0 * cm, f"Branch: {branch}")
        canv.drawRightString(PW - 1.6 * cm, PH - 2.0 * cm, f"Date: {logic.fmt_date(session.get('date'))}")
        canv.setFont("DVB", 8.5)
        canv.drawString(1.6 * cm, 1.1 * cm, f"Inventory Manager: {manager}")
        canv.drawRightString(PW - 1.6 * cm, 1.1 * cm, f"COO: {approver}")
        canv.setFont("DV", 7.5)
        canv.setFillColor(GREY)
        canv.drawCentredString(PW / 2, 0.6 * cm, f"Page {d.page}")
        canv.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# =============================================================== purchases register
def register_pdf(db: dict, start: date | None, end: date | None, supplier_id: str | None = None) -> bytes:
    _fonts()
    st = db["settings"]
    cur = st.get("currency", "EGP")
    PW, PH = landscape(A4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.0 * cm, rightMargin=1.0 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.4 * cm, title="Purchases register")
    period = f"{logic.fmt_date(start) or '…'} → {logic.fmt_date(end) or '…'}"
    sname = logic.supplier_name(db, supplier_id) if supplier_id else "All suppliers"
    head = Table([[Praw(f"<b>Annual Kits &amp; Consumables Purchasing</b><br/>"
                        f"<font size='9' color='#666666'>{escape(period)} · {escape(sname)} · amounts in {cur}</font>",
                        S(size=14, leading=17)), _logo(3.4)]],
                 colWidths=[PW - 2 * cm - 3.8 * cm, 3.8 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story = [head, Spacer(1, 0.3 * cm)]
    cols = ["Date", "Supplier", "Item description", "Cat.", "Brand", "Kit / pack", "Lot", "Exp",
            "Qty", "Unit price", "Total", "Payment", "Paid on", "Notes"]
    widths = [1.8, 2.4, 4.2, 2.0, 1.8, 1.6, 1.4, 1.3, 1.0, 1.6, 1.8, 2.2, 1.8, 2.8]
    h = S(size=7.6, font="DVC", bold=True, align=TA_CENTER, color=colors.white)
    c = S(size=7.4, font="DVC", leading=8.8)
    cr = S(size=7.4, font="DVC", leading=8.8, align=TA_RIGHT)
    rows = [[P(x, h) for x in cols]]
    style = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#999999")),
             ("BACKGROUND", (0, 0), (-1, 0), ORANGE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]
    g_total = g_paid = g_ret = 0.0
    invs = logic.invoices_in(db, start, end, supplier_id)
    for inv in invs:
        t = logic.invoice_totals(inv)
        g_total += t["total"]
        g_paid += t["paid"]
        sname_i = inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id"))
        first = True
        for ln in inv.get("lines", []):
            lt = logic.line_total(ln)
            kind = logic.clean_str(ln.get("kind")) or "Purchase"
            if kind == "Return":
                g_ret += -lt
            note = logic.clean_str(ln.get("notes"))
            if kind != "Purchase":
                note = (kind + (" – " + note if note else ""))
            if logic.to_float(ln.get("discount_pct")):
                note = (note + f" (−{logic.fmt_num(ln['discount_pct'])}%)").strip()
            rows.append([P(logic.fmt_date(inv["date"]) if first else "", c),
                         P(sname_i if first else "", c), P(ln.get("description", ""), c),
                         P(ln.get("category", ""), c), P(ln.get("brand", ""), c), P(ln.get("kit", ""), c),
                         P(ln.get("lot", ""), c), P(logic.fmt_exp(ln.get("exp")), c),
                         P(logic.fmt_num(ln.get("qty")), cr), P(logic.fmt_num(ln.get("unit_price")), cr),
                         P(logic.fmt_num(lt), cr), "", "", P(note, c)])
            if kind == "Return":
                style.append(("TEXTCOLOR", (0, len(rows) - 1), (-1, len(rows) - 1), RED))
            first = False
        if t["delivery"]:
            rows.append(["", "", P("Delivery fee", c), "", "", "", "", "", "", "",
                         P(logic.fmt_num(t["delivery"]), cr), "", "", ""])
        if t["discount"]:
            rows.append(["", "", P("Invoice discount", c), "", "", "", "", "", "", "",
                         P("−" + logic.fmt_num(t["discount"]), cr), "", "", ""])
        pays = [p for p in inv.get("payments", []) if logic.clean_str(p.get("method")) != "Credit note"]
        credit = sum(logic.to_float(p.get("amount")) or 0 for p in inv.get("payments", [])
                     if logic.clean_str(p.get("method")) == "Credit note")
        pay_txt = "\n".join(f"{logic.fmt_num(p.get('amount'))} {p.get('method', '')}" for p in pays)
        if credit:
            pay_txt += ("\n" if pay_txt else "") + f"{logic.fmt_num(credit)} credit"
        pay_dates = "\n".join(logic.fmt_date(p.get("date")) for p in pays)
        bal = t["balance"]
        tot_note = f"{inv['id']} · {t['status']}" + (f" · balance {logic.fmt_num(bal)}" if abs(bal) >= 0.5 else "")
        if inv.get("needs_review"):
            tot_note += " · ⚠ review"
        rows.append(["", "", P("Invoice total", S(size=7.4, font="DVC", bold=True)), "", "", "", "", "", "", "",
                     P(logic.fmt_num(t["total"]), S(size=7.6, font="DVC", bold=True, align=TA_RIGHT)),
                     P(pay_txt, c), P(pay_dates, c), P(tot_note, c)])
        style.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), LIGHT))
        style.append(("LINEBELOW", (0, len(rows) - 1), (-1, len(rows) - 1), 0.9, colors.black))
    t = Table(rows, colWidths=[w * cm for w in widths], repeatRows=1)
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 0.4 * cm))
    summ = [["Invoices", str(len(invs))], ["Purchases (net of returns)", logic.fmt_num(g_total)],
            ["Returns", logic.fmt_num(g_ret)], ["Paid", logic.fmt_num(g_paid)],
            ["Balance due", logic.fmt_num(g_total - g_paid)]]
    st_t = Table([[P(a, S(size=9, bold=True)), P(b, S(size=9, align=TA_RIGHT))] for a, b in summ],
                 colWidths=[5.5 * cm, 3.5 * cm])
    st_t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                              ("BACKGROUND", (0, 0), (0, -1), LIGHT)]))
    by_sup = defaultdict(lambda: [0.0, 0.0])
    for inv in invs:
        tt = logic.invoice_totals(inv)
        k = inv.get("supplier_name") or logic.supplier_name(db, inv.get("supplier_id"))
        by_sup[k][0] += tt["total"]
        by_sup[k][1] += tt["balance"]
    sup_rows = [[P("Supplier", S(size=8.5, bold=True)), P("Purchases", S(size=8.5, bold=True, align=TA_RIGHT)),
                 P("Balance", S(size=8.5, bold=True, align=TA_RIGHT))]]
    for k, (a, b) in sorted(by_sup.items(), key=lambda kv: -kv[1][0]):
        sup_rows.append([P(k, S(size=8.5)), P(logic.fmt_num(a), S(size=8.5, align=TA_RIGHT)),
                         P(logic.fmt_num(b), S(size=8.5, align=TA_RIGHT))])
    sup_t = Table(sup_rows, colWidths=[5 * cm, 3 * cm, 3 * cm])
    sup_t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("BACKGROUND", (0, 0), (-1, 0), LIGHT)]))
    both = Table([[st_t, sup_t]], colWidths=[10 * cm, 12 * cm])
    both.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(KeepTogether([both]))

    def on_page(canv, d):
        canv.setFont("DV", 7.5)
        canv.setFillColor(GREY)
        canv.drawString(1.0 * cm, 0.7 * cm, f"{st.get('lab_name', '')} – purchases register")
        canv.drawRightString(PW - 1.0 * cm, 0.7 * cm, f"Page {d.page}")
        canv.setFillColor(colors.black)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# =============================================================== supplier statement
def statement_pdf(db: dict, supplier_id: str, start: date | None, end: date | None) -> bytes:
    _fonts()
    st = db["settings"]
    sup = logic.index(db, "suppliers").get(supplier_id, {})
    rows_data, opening = logic.supplier_statement(db, supplier_id, start, end)
    W, H = A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.4 * cm, rightMargin=1.4 * cm,
                            topMargin=1.0 * cm, bottomMargin=2.6 * cm, title="Supplier statement")
    head = Table([[Praw(f"<b>Supplier statement</b><br/><font size='10'>{escape(sup.get('name', ''))}"
                        f"{(' · ' + escape(sup.get('phone', ''))) if sup.get('phone') else ''}</font><br/>"
                        f"<font size='8.5' color='#666666'>Period: {logic.fmt_date(start) or 'start'} → "
                        f"{logic.fmt_date(end) or 'today'} · {st.get('currency', 'EGP')}</font>",
                        S(size=15, leading=18)), _logo(3.6)]],
                 colWidths=[W - 2.8 * cm - 4 * cm, 4 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    h = S(size=8.5, font="DVC", bold=True, color=colors.white, align=TA_CENTER)
    c = S(size=8.3, font="DVC", leading=10)
    cr = S(size=8.3, font="DVC", leading=10, align=TA_RIGHT)
    rows = [[P(x, h) for x in ["Date", "Type", "Ref", "Description", "Debit", "Credit", "Balance"]]]
    rows.append([P(logic.fmt_date(start) if start else "", c), "", "",
                 P("Opening balance", S(size=8.3, font="DVC", bold=True)), "", "",
                 P(logic.fmt_num(opening), cr)])
    td = tc = 0.0
    for r in rows_data:
        td += r["debit"]
        tc += r["credit"]
        rows.append([P(logic.fmt_date(r["date"]), c), P(r["type"], c),
                     P(r["ref"] + (f"\n{r['doc']}" if r.get("doc") else ""), c), P(r["desc"], c),
                     P(logic.fmt_num(r["debit"]) if r["debit"] else "", cr),
                     P(logic.fmt_num(r["credit"]) if r["credit"] else "", cr),
                     P(logic.fmt_num(r["balance"]), cr)])
    closing = rows_data[-1]["balance"] if rows_data else opening
    rows.append(["", "", "", P("Totals / closing balance", S(size=8.5, bold=True)),
                 P(logic.fmt_num(td), S(size=8.5, bold=True, align=TA_RIGHT)),
                 P(logic.fmt_num(tc), S(size=8.5, bold=True, align=TA_RIGHT)),
                 P(logic.fmt_num(closing), S(size=8.5, bold=True, align=TA_RIGHT))])
    t = Table(rows, colWidths=[2.0 * cm, 1.6 * cm, 2.5 * cm, 5.7 * cm, 2.1 * cm, 2.1 * cm, 2.2 * cm], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#999999")),
                           ("BACKGROUND", (0, 0), (-1, 0), ORANGE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BACKGROUND", (0, -1), (-1, -1), LIGHT), ("BACKGROUND", (0, 1), (-1, 1), LIGHT)]))
    msg = ("Balance due to supplier" if closing > 0.5 else
           "Credit with supplier" if closing < -0.5 else "Account settled")
    story = [head, Spacer(1, 0.4 * cm), t, Spacer(1, 0.3 * cm),
             P(f"{msg}: {logic.fmt_num(abs(closing))} {st.get('currency', 'EGP')}", S(size=11, bold=True))]

    def on_page(canv, d):
        _footer_branches(canv, st, W, margin=1.4 * cm)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


# =============================================================== expiry report
def expiry_pdf(db: dict, days: int) -> bytes:
    _fonts()
    st = db["settings"]
    data = logic.expiry_alerts(db, days)
    W, H = A4
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.4 * cm, rightMargin=1.4 * cm,
                            topMargin=1.0 * cm, bottomMargin=1.5 * cm, title="Expiry report")
    head = Table([[Praw(f"<b>Expiry report</b><br/><font size='9' color='#666666'>Expired lots and lots "
                        f"expiring within {days} days · latest counts · printed {logic.fmt_date(date.today())}</font>",
                        S(size=15, leading=18)), _logo(3.6)]], colWidths=[W - 2.8 * cm - 4 * cm, 4 * cm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    cols = ["Branch", "Item", "Brand", "Place", "Qty", "Exp", "Days left", "State", "Count"]
    h = S(size=8.5, bold=True, color=colors.white, align=TA_CENTER)
    c = S(size=8.2, leading=10)
    rows = [[P(x, h) for x in cols]]
    style = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#999999")),
             ("BACKGROUND", (0, 0), (-1, 0), ORANGE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for r in data:
        rows.append([P(str(r[k]), c) for k in cols])
        if r["Days left"] < 0:
            style.append(("TEXTCOLOR", (0, len(rows) - 1), (-1, len(rows) - 1), RED))
    if len(rows) == 1:
        rows.append([P("Nothing expired or expiring in this window.", c)] + [""] * 8)
    t = Table(rows, colWidths=[1.4 * cm, 3.6 * cm, 2.3 * cm, 1.4 * cm, 2.0 * cm, 1.5 * cm, 1.6 * cm,
                               2.4 * cm, 2.0 * cm], repeatRows=1)
    t.setStyle(TableStyle(style))
    doc.build([head, Spacer(1, 0.4 * cm), t])
    return buf.getvalue()
