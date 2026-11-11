"""Build the starter database (catalog + suppliers + the two sheet counts + purchase history).

Usage:  python -m core.build_seed [path/to/annual_purchasing.xlsx]
"""
from __future__ import annotations

import sys
from collections import defaultdict

from . import config, importers, logic, seed, storage


def build(purchases_xlsx: str | None = None) -> tuple[dict, dict]:
    db = storage.empty_db()
    ops = []
    order = 0
    for group, rows in (("Supplies", seed.SUPPLIES), ("Chemicals", seed.CHEMICALS)):
        for name, typ, unit, punit, brand, branches, aliases in rows:
            order += 1
            ops.append(storage.op_insert("items", {
                "name": name, "group": group, "type": typ, "unit": unit,
                "purchase_unit": punit, "default_brand": brand, "default_supplier_id": "",
                "branches": branches, "min_qty": 0, "reorder_qty": 0, "aliases": aliases,
                "active": name not in seed.INACTIVE, "notes": "", "order": order}))
    for name, aliases, supplies in seed.SUPPLIERS:
        ops.append(storage.op_insert("suppliers", {
            "name": name, "aliases": aliases, "phone": "", "whatsapp": "", "email": "",
            "contact": "", "address": "", "supplies": supplies, "payment_terms": "",
            "notes": "", "active": True}))
    db, _ = storage.apply_ops(db, ops)

    by_name = {i["name"]: i["id"] for i in db["items"]}
    ops = []
    for cnt in (seed.COUNT_CHEMICALS, seed.COUNT_SUPPLIES):
        lines = []
        for t in cnt["lines"]:
            d = dict(zip(seed.LINE_KEYS, t))
            d["item_id"] = by_name[d.pop("item")]
            d["active_exp"] = logic.parse_exp(d["active_exp"]) or ""
            d["stock_exp"] = logic.parse_exp(d["stock_exp"]) or ""
            lines.append(d)
        ops.append(storage.op_insert("counts", {
            "date": cnt["date"], "group": cnt["group"], "branch": cnt["branch"],
            "counted_by": cnt["counted_by"], "approved_by": cnt["approved_by"],
            "state": "Final", "notes": cnt["source"], "source": "seed",
            "created_by": "setup", "lines": lines}, year=int(cnt["date"][:4])))
    db, _ = storage.apply_ops(db, ops)

    report = {}
    if purchases_xlsx:
        invoices, report = importers.import_legacy_purchases(purchases_xlsx, db)
        ops = [storage.op_insert("suppliers", {"name": n, "aliases": "", "phone": "",
                                               "whatsapp": "", "email": "", "contact": "",
                                               "address": "", "supplies": "", "payment_terms": "",
                                               "notes": "added by import", "active": True})
               for n in report["new_suppliers"]]
        db, _ = storage.apply_ops(db, ops)
        importers.link_suppliers(db, invoices)
        invoices.sort(key=lambda i: (i["date"], i["import_ref"]))
        ops = [storage.op_insert("invoices", inv, year=int(inv["date"][:4])) for inv in invoices]
        db, _ = storage.apply_ops(db, ops)
        set_default_suppliers(db)
    db["audit"].append({"ts": storage.now_iso(), "user": "setup",
                        "action": "Starter database created", "ref": ""})
    return db, report


def set_default_suppliers(db: dict, overwrite: bool = False) -> int:
    """Default supplier & brand-less items ← most recent purchase in history."""
    last = logic.last_purchases(db)
    n = 0
    for it in db["items"]:
        lp = last.get(it["id"])
        if lp and lp.get("supplier_id") and (overwrite or not it.get("default_supplier_id")):
            it["default_supplier_id"] = lp["supplier_id"]
            n += 1
    return n


if __name__ == "__main__":
    xlsx = sys.argv[1] if len(sys.argv) > 1 else None
    db, rep = build(xlsx)
    storage.write_json_atomic(config.LOCAL_DB_PATH, db)
    print("items", len(db["items"]), "suppliers", len(db["suppliers"]),
          "counts", len(db["counts"]), "invoices", len(db["invoices"]))
    if rep:
        print("unmatched:", rep["unmatched_items"])
        print("date fixes:", rep["date_fixes"])
        print("new suppliers:", rep["new_suppliers"])
