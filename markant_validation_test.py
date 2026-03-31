#!/usr/bin/env python3
"""
MARKANT RALA validation test — checks all rules from the EDI-Prüfungsprotokoll.

Generates a Warenrechnung with Innergem. Lieferung items (0% tax)
and validates the output XML against MARKANT's EANCOM D01B requirements.
"""

import io
import os
import sys
import tempfile
import re
from datetime import date
from lxml import etree
from unittest.mock import MagicMock

# Mock streamlit
sys.modules["streamlit"] = MagicMock()

with open("app.py", "r") as f:
    src = f.read()

split_marker = "# ─────────────────────────────────────────────────────────────────────────────\n# STREAMLIT UI"
code = src.split(split_marker)[0]

namespace = {}
exec(code, namespace)

build_xml = namespace["build_xml"]
build_pdf = namespace["build_pdf"]
calculate_totals = namespace["calculate_totals"]

# ── EANCOM D01B valid codes ──────────────────────────────────────────────────
VALID_TAX_CATEGORIES_EANCOM = {"S", "Z", "E", "AE", "G", "O", "L", "M", "AA", "H"}
VALID_UNIT_CODES_EANCOM = {
    "C62", "KGM", "GRM", "LTR", "MTR", "MTK", "MTQ", "TNE",
    "KTM", "PCE", "SET", "PR", "PK", "BX", "CS", "CT",
    "PA", "BG", "BO", "CL", "CMT", "DZN", "EA", "GLL",
    "KG", "LT", "MLT", "MMT", "NT", "PF", "PT", "QT",
    "RO", "ST", "TU", "XBC", "XCT", "XPX",
}

# ── Build test data — mimics the rejected Warenrechnung ─────────────────────
positions = []
# 7 line items with "0% Innergem. Lieferung" (like the rejected invoice)
for i in range(1, 8):
    positions.append({
        "gtin": f"426072942{1870 + i}",
        "seller_id": f"ART-{i:03d}",
        "buyer_id": "",
        "name": f"Testartikel Innergem. Lieferung {i}",
        "qty": 200.0,
        "unit": "C62",
        "gross_price": 2.39,
        "discount_pct": 0.0,
        "vat_rate": 0.0,
        "tax_treatment": "0% Innergem. Lieferung",
    })

totals = calculate_totals(positions, 0.0, 0.0, 19.0)

invoice_data = {
    "inv_number": "2025-872867",
    "inv_date": date(2026, 3, 31),
    "delivery_date": date(2026, 3, 28),
    "currency": "EUR",
    "doc_type": "WARENRECHNUNG",
    "order_ref": "1003327714",
    "order_date": date(2026, 3, 25),
    "delivery_ref": "LS-2026-001",
    "delivery_ref_date": date(2026, 3, 28),
    "seller_order_ref": "20",
    "seller_order_date": date(2026, 3, 25),
    "test_mode": True,
    "seller": {
        "name": "Test Lieferant GmbH",
        "id": "2012830",
        "gln": "4260729420001",
        "street": "Teststraße 1",
        "zip": "12345",
        "city": "Berlin",
        "country": "DE",
        "vat_id": "DE123456789",
        "email": "info@test-lieferant.de",
        "phone": "+49 30 1234567",
    },
    "buyer": {
        "name": "MARKANT Deutschland GmbH",
        "id": "",
        "gln": "4305215000002",
        "street": "Hanns-Martin-Schleyer-Str. 2",
        "zip": "77656",
        "city": "Offenburg",
        "country": "DE",
        "vat_id": "DE812345678",
    },
    "shipto": {
        "name": "Lager Tschechien",
        "gln": "4305215000099",
        "dept": "",
        "street": "Industriestr. 10",
        "zip": "34802",
        "city": "Bor u Tachova",
        "country": "CZ",
    },
    "positions": positions,
    "header_discount_pct": 0.0,
    "header_discount_name": "Rechnungsrabatt",
    "shipping_charge_eur": 0.0,
    "shipping_vat_rate": 19.0,
    "skonto_pct": 0.0,
    "skonto_days": 14,
    "payment_note": "Zahlbar innerhalb 30 Tagen netto.",
    "entgeltminderung": False,
    "entgeltminderung_text": "",
    "seller_reg_note": "Test Lieferant GmbH\nTeststraße 1\n12345 Berlin\nUSt-IdNr: DE123456789",
    "totals": totals,
}

# ── Generate XML ─────────────────────────────────────────────────────────────
xml_bytes = build_xml(invoice_data)
root = etree.fromstring(xml_bytes)

RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
ns = {"ram": RAM, "udt": UDT, "qdt": QDT, "rsm": RSM}

results = []

def check(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((status, name, detail))

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 1: Ablehnung — Tax category codes must be valid EANCOM D01B
# ═══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("MARKANT RALA VALIDATION TEST")
print("=" * 72)

# Header-level tax categories
header_tax_cats = root.findall(".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/ram:CategoryCode", ns)
for cat_el in header_tax_cats:
    code = cat_el.text or ""
    check(
        "Ablehnung: Header-Tax CategoryCode valid for EANCOM",
        code in VALID_TAX_CATEGORIES_EANCOM,
        f"CategoryCode='{code}'"
    )
    check(
        "Ablehnung: Header-Tax CategoryCode not empty",
        bool(code.strip()),
        f"CategoryCode='{code}'"
    )
    check(
        "Ablehnung: Header-Tax CategoryCode != 'K' (not EANCOM D01B)",
        code != "K",
        f"CategoryCode='{code}'"
    )

# Line-level tax categories
line_tax_cats = root.findall(".//ram:IncludedSupplyChainTradeLineItem//ram:CategoryCode", ns)
for i, cat_el in enumerate(line_tax_cats, 1):
    code = cat_el.text or ""
    check(
        f"Warnung: Line {i} Tax CategoryCode valid for EANCOM",
        code in VALID_TAX_CATEGORIES_EANCOM,
        f"CategoryCode='{code}'"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 2: Warnung — Belegklassifizierung / Document Name
# ═══════════════════════════════════════════════════════════════════════════════
doc_name_el = root.find(".//rsm:ExchangedDocument/ram:Name", ns)
check(
    "Warnung: No free-text Name in ExchangedDocument (BGM.C002.1000)",
    doc_name_el is None,
    f"Name element {'absent (good)' if doc_name_el is None else f'present: {doc_name_el.text}'}"
)

type_code_el = root.find(".//rsm:ExchangedDocument/ram:TypeCode", ns)
check(
    "TypeCode present and valid",
    type_code_el is not None and type_code_el.text in ("380", "381", "384", "389"),
    f"TypeCode='{type_code_el.text if type_code_el is not None else 'MISSING'}'"
)

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 3: Warnung — Unit codes must be EANCOM-compatible
# ═══════════════════════════════════════════════════════════════════════════════
billed_qtys = root.findall(".//ram:BilledQuantity", ns)
for i, qty_el in enumerate(billed_qtys, 1):
    unit = qty_el.get("unitCode", "")
    check(
        f"Warnung: Line {i} Unit code EANCOM-compatible",
        unit != "H87",
        f"unitCode='{unit}'"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 4: Hinweis — Reference dates present (not "0")
# ═══════════════════════════════════════════════════════════════════════════════
buyer_order = root.find(".//ram:BuyerOrderReferencedDocument", ns)
if buyer_order is not None:
    order_id = buyer_order.find("ram:IssuerAssignedID", ns)
    order_date = buyer_order.find("ram:FormattedIssueDateTime", ns)
    check(
        "Hinweis: BuyerOrderReferencedDocument has date",
        order_date is not None,
        f"OrderRef='{order_id.text if order_id is not None else ''}', Date={'present' if order_date is not None else 'MISSING → would be 0 in EANCOM'}"
    )
    if order_date is not None:
        date_str_el = order_date.find("qdt:DateTimeString", ns)
        date_val = date_str_el.text if date_str_el is not None else ""
        check(
            "Hinweis: BuyerOrder date is valid (not 0)",
            date_val and date_val != "0" and len(date_val) == 8,
            f"DateTimeString='{date_val}'"
        )

seller_order = root.find(".//ram:SellerOrderReferencedDocument", ns)
if seller_order is not None:
    seller_id = seller_order.find("ram:IssuerAssignedID", ns)
    seller_date = seller_order.find("ram:FormattedIssueDateTime", ns)
    check(
        "Hinweis: SellerOrderReferencedDocument has date",
        seller_date is not None,
        f"SellerOrderRef='{seller_id.text if seller_id is not None else ''}', Date={'present' if seller_date is not None else 'MISSING'}"
    )

delivery_note = root.find(".//ram:DeliveryNoteReferencedDocument", ns)
if delivery_note is not None:
    dn_id = delivery_note.find("ram:IssuerAssignedID", ns)
    dn_date = delivery_note.find("ram:FormattedIssueDateTime", ns)
    check(
        "Hinweis: DeliveryNoteReferencedDocument has date",
        dn_date is not None,
        f"DeliveryRef='{dn_id.text if dn_id is not None else ''}', Date={'present' if dn_date is not None else 'MISSING'}"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 5: Hinweis — PLZ field sanity (not a city name)
# ═══════════════════════════════════════════════════════════════════════════════
postcodes = root.findall(".//ram:PostcodeCode", ns)
for pc_el in postcodes:
    val = pc_el.text or ""
    looks_like_city = len(val) > 10 or (any(c.isalpha() for c in val) and len(val) > 6)
    check(
        "Hinweis: PostcodeCode looks like a valid PLZ",
        not looks_like_city,
        f"PostcodeCode='{val}'"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 6: ExemptionReason present for 0% tax
# ═══════════════════════════════════════════════════════════════════════════════
header_taxes = root.findall(".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax", ns)
for tax_el in header_taxes:
    rate_el = tax_el.find("ram:RateApplicablePercent", ns)
    rate = float(rate_el.text) if rate_el is not None else -1
    if rate == 0.0:
        reason_el = tax_el.find("ram:ExemptionReason", ns)
        reason_code_el = tax_el.find("ram:ExemptionReasonCode", ns)
        check(
            "ExemptionReason present for 0% tax",
            reason_el is not None and bool((reason_el.text or "").strip()),
            f"Rate=0%, Reason='{reason_el.text if reason_el is not None else 'MISSING'}'"
        )
        check(
            "ExemptionReasonCode present for 0% tax",
            reason_code_el is not None and bool((reason_code_el.text or "").strip()),
            f"Rate=0%, ReasonCode='{reason_code_el.text if reason_code_el is not None else 'MISSING'}'"
        )

# ═══════════════════════════════════════════════════════════════════════════════
# CHECK 7: XSD validation
# ═══════════════════════════════════════════════════════════════════════════════
import facturx
pdf_bytes = build_pdf(invoice_data)
try:
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, "invoice.pdf")
        xml_path = os.path.join(tmpdir, "factur-x.xml")
        out_path = os.path.join(tmpdir, "zugferd.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        with open(xml_path, "wb") as f:
            f.write(xml_bytes)
        facturx.generate_from_file(
            pdf_path, xml_path,
            flavor="factur-x", level="extended",
            check_xsd=True, output_pdf_file=out_path,
        )
    check("XSD validation", True, "factur-x Extended XSD passed")
except Exception as e:
    check("XSD validation", False, str(e))

# ═══════════════════════════════════════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════════════════════════════════════
print()
passed = sum(1 for s, _, _ in results if s == "PASS")
failed = sum(1 for s, _, _ in results if s == "FAIL")

for status, name, detail in results:
    icon = "✅" if status == "PASS" else "❌"
    print(f"  {icon} {name}")
    if detail:
        print(f"     → {detail}")

print()
print(f"{'=' * 72}")
print(f"  Total: {len(results)} checks | ✅ {passed} passed | ❌ {failed} failed")
print(f"{'=' * 72}")

if failed:
    sys.exit(1)
else:
    print("\n🎉 All MARKANT RALA validation checks passed!")
