"""Constants and default settings for Orange Lab Inventory & Purchasing."""
from __future__ import annotations

import os

APP_TITLE = "Orange Lab – Inventory & Purchasing"
APP_SHORT = "Orange Inventory"
DB_VERSION = 1

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
FONTS_DIR = os.path.join(ASSETS_DIR, "fonts")
LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOCAL_DB_PATH = os.path.join(DATA_DIR, "inventory_db.json")      # your live data (never shipped in updates)
STARTER_DB_PATH = os.path.join(DATA_DIR, "starter_db.json")     # catalog + counts + old purchases, used once

# ---------------------------------------------------------------- branches
BRANCHES = ["Diamond", "La Cite"]
BRANCH_CODE = {"Diamond": "D", "La Cite": "L"}
CODE_BRANCH = {"D": "Diamond", "L": "La Cite"}
BRANCH_LABEL = {"Diamond": "Diamond", "La Cite": "La Cité", "All": "All"}
LINE_BRANCHES = ["D", "L", "D&L"]          # branch codes used on count lines

# ---------------------------------------------------------------- items
GROUPS = ["Supplies", "Chemicals"]
ITEM_TYPES = {
    "Supplies": ["Tube", "Consumable", "Syringe", "Tips", "Gloves", "Equipment"],
    "Chemicals": ["Chemical", "Cards", "Coatron", "I chroma", "Hematology",
                  "Control/Calibrator", "Stain", "Other"],
}
ALL_TYPES = sorted({t for v in ITEM_TYPES.values() for t in v})
UNITS = ["piece", "tube", "pack", "box", "kit", "test", "ml", "bottle", "vial",
         "card", "roll", "kg", "sachet", "set"]

# purchase-register category (as in the annual purchasing sheet)
TYPE_TO_CATEGORY = {
    "Tube": "consumables", "Consumable": "consumables", "Syringe": "consumables",
    "Tips": "consumables", "Gloves": "consumables", "Equipment": "equipment",
    "Chemical": "chemical", "Coatron": "chemical", "I chroma": "chemical",
    "Hematology": "chemical", "Control/Calibrator": "chemical", "Stain": "chemical",
    "Other": "chemical", "Cards": "cards",
}
CATEGORIES = ["chemical", "cards", "consumables", "equipment", "service", "other"]

# ---------------------------------------------------------------- inventory status
STATUS_CHOICES = ["Auto", "G", "B", "-"]          # Auto = let the app decide
STATUS_LABEL = {"G": "Good", "B": "Need to buy", "-": "Not used / N.A."}
AVAIL_AVAILABLE = "Available"
AVAIL_EXPIRED = "Expired"
AVAIL_SMALL = "Small amount"
AVAIL_NONE = "Not available"
AVAIL_COLORS = {                                  # same legend as the paper sheets
    AVAIL_AVAILABLE: "#000000",
    AVAIL_EXPIRED: "#E00000",
    AVAIL_SMALL: "#2F6FD0",
    AVAIL_NONE: "#00A04A",
}

# ---------------------------------------------------------------- purchasing
PO_STATUSES = ["Draft", "Sent", "Partially received", "Received", "Cancelled"]
PO_OPEN_STATUSES = ["Draft", "Sent", "Partially received"]
PO_FLAGS = ["Urgent", "For Review", "Please Comment", "Please Reply", "Please Recycle"]
PO_CHANNELS = ["Email", "What's app", "Print"]
LINE_KINDS = ["Purchase", "Return", "Free", "Gift"]
PAYMENT_METHODS = ["Vodafone Cash", "InstaPay", "Cash", "Bank transfer", "Cheque",
                   "Credit note", "Other"]

# ---------------------------------------------------------------- default settings
DEFAULT_SETTINGS = {
    "lab_name": "Orange Lab",
    "po_from": "Orange lab October",
    "po_mobile": "",
    "po_blank_rows": 23,
    "expiry_warn_days": 60,
    "currency": "EGP",
    "consumption_alert_pct": 30,       # deviation that flags abnormal consumption
    "reagent_overhead_pct": 15,        # QC / calibration / repeats allowance on test counts
    "general_manager": "Dr/Tarik El shafie",
    "coo": "Dr/ Hussein Ali",
    "inventory_manager_supplies": "Dr/Shereen Ahmed",
    "inventory_manager_chemicals": "Dr/Abdulmalek Mahmoud",
    "po_signature_left_title": "Inventory manager",
    "po_signature_right_title": "General manager",
    "branches": {
        "Diamond": {
            "label": "Diamond",
            "address_ar": "أكتوبر – المحور الخدمي – أمام دايموند مول وجامعة أكتوبر",
            "phones": "01097902820 - 0236969293",
        },
        "La Cite": {
            "label": "La Cité",
            "address_ar": "المحور المركزي – أعلى معرض سيارات زون أوتو – وبجوار خير زمان الجديد",
            "phones": "01032847185 - 01032847186",
        },
    },
}
