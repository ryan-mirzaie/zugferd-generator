#!/usr/bin/env python3
"""Smoke test for ZUGFeRD generator – runs without Streamlit."""

import io
import os
import sys
import tempfile
from datetime import date
from unittest.mock import MagicMock

import facturx
from pypdf import PdfReader

# Mock streamlit
sys.modules["streamlit"] = MagicMock()

# Load only the non-UI portion of app.py (up to the STREAMLIT UI section)
with open("app.py", "r") as f:
    src = f.read()

# Extract everything before the STREAMLIT UI block
split_marker = "# ─────────────────────────────────────────────────────────────────────────────\n# STREAMLIT UI"
code = src.split(split_marker)[0]

namespace = {}
exec(code, namespace)

build_xml = namespace["build_xml"]
build_pdf = namespace["build_pdf"]
calculate_totals = namespace["calculate_totals"]

# ── Build test data ────────────────────────────────────────────────────────────
positions = [
    {
        "gtin": "4012345678901",
        "seller_id": "SKU-PROT-001",
        "buyer_id": "",
        "name": "SHEKO Protein Shake Schokolade 500g",
        "qty": 10.0,
        "unit": "H87",
        "gross_price": 24.99,
        "discount_pct": 5.0,
        "vat_rate": 19.0,
    },
    {
        "gtin": "4012345678902",
        "seller_id": "SKU-VIT-002",
        "buyer_id": "",
        "name": "SHEKO Vitamin Drink Beere 330ml",
        "qty": 50.0,
        "unit": "H87",
        "gross_price": 2.49,
        "discount_pct": 0.0,
        "vat_rate": 7.0,
    },
    {
        "gtin": "",
        "seller_id": "DISP-001",
        "buyer_id": "",
        "name": "Displaymaterial / Werbemittel",
        "qty": 1.0,
        "unit": "H87",
        "gross_price": 0.0,
        "discount_pct": 0.0,
        "vat_rate": 0.0,
    },
]

totals = calculate_totals(positions, 2.0, 15.0, 19.0)

invoice_data = {
    "inv_number": "RE-2024-SMOKE-001",
    "inv_date": date(2024, 3, 5),
    "delivery_date": date(2024, 3, 4),
    "currency": "EUR",
    "doc_type": "WARENRECHNUNG",
    "order_ref": "4500123456",
    "delivery_ref": "LS-2024-001",
    "seller_order_ref": "AU-2024-001",
    "test_mode": True,
    "seller": {
        "name": "SHEKO GmbH",
        "id": "",
        "gln": "",
        "street": "Große Elbstraße 39",
        "zip": "22767",
        "city": "Hamburg",
        "country": "DE",
        "vat_id": "DE999999999",
        "email": "buchhaltung@sheko.de",
        "phone": "+49 40 123456789",
    },
    "buyer": {
        "name": "Markant Handels und Service GmbH",
        "id": "KD-001234",
        "gln": "4012345000007",
        "street": "Römerstraße 30",
        "zip": "74722",
        "city": "Buchen",
        "country": "DE",
    },
    "shipto": {
        "name": "Markant Handels und Service GmbH",
        "gln": "4012345000007",
        "dept": "",
        "street": "Römerstraße 30",
        "zip": "74722",
        "city": "Buchen",
        "country": "DE",
    },
    "positions": positions,
    "header_discount_pct": 2.0,
    "header_discount_name": "Rechnungsrabatt",
    "shipping_charge_eur": 15.0,
    "shipping_vat_rate": 19.0,
    "skonto_pct": 2.0,
    "skonto_days": 10,
    "payment_note": "Zahlbar innerhalb 30 Tagen netto.",
    "entgeltminderung": True,
    "seller_reg_note": "SHEKO GmbH\nGroße Elbstraße 39\n22767 Hamburg\nUSt-IdNr: DE999999999",
    "totals": totals,
}

xml_bytes = build_xml(invoice_data)
pdf_bytes = build_pdf(invoice_data)

with tempfile.TemporaryDirectory() as tmpdir:
    pdf_path = os.path.join(tmpdir, "invoice.pdf")
    xml_path = os.path.join(tmpdir, "factur-x.xml")
    out_path = os.path.join(tmpdir, "zugferd.pdf")

    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    with open(xml_path, "wb") as f:
        f.write(xml_bytes)

    facturx.generate_from_file(
        pdf_path,
        xml_path,
        flavor="factur-x",
        level="extended",
        check_xsd=True,
        output_pdf_file=out_path,
    )

    with open(out_path, "rb") as f:
        hybrid_pdf_bytes = f.read()

assert b"<rsm:CrossIndustryInvoice" in xml_bytes, "XML missing root element"
assert b"RE-2024-SMOKE-001" in xml_bytes, "XML missing invoice number"
assert pdf_bytes[:4] == b"%PDF", "PDF does not start with %PDF"
assert hybrid_pdf_bytes[:4] == b"%PDF", "Hybrid PDF does not start with %PDF"

reader = PdfReader(io.BytesIO(hybrid_pdf_bytes))
unembedded_fonts = []
for page in reader.pages:
    resources = page.get("/Resources") or {}
    fonts = resources.get("/Font") or {}
    for font_ref in fonts.values():
        font = font_ref.get_object()
        descriptor = font.get("/FontDescriptor")
        if descriptor is None:
            unembedded_fonts.append(str(font.get("/BaseFont")))
            continue
        descriptor = descriptor.get_object()
        if not any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")):
            unembedded_fonts.append(str(font.get("/BaseFont")))

assert not unembedded_fonts, f"Unembedded fonts found in hybrid PDF: {sorted(set(unembedded_fonts))}"

print(f"XML size:    {len(xml_bytes):,} bytes")
print(f"PDF size:    {len(pdf_bytes):,} bytes")
print(f"Hybrid PDF:  {len(hybrid_pdf_bytes):,} bytes")
print(f"Grand total: {float(totals['grand_total']):.2f} EUR")
print("✅ Smoke test passed")
