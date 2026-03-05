# ZUGFeRD Rechnungsgenerator – SHEKO GmbH

A Streamlit web app that generates ZUGFeRD Extended 2.3 hybrid PDF/XML invoices (Factur-X) for sending to retail customers like Markant.

## What it does

- Generates compliant hybrid PDF/A-3 invoices with an embedded `factur-x.xml` (ZUGFeRD 2.3 Extended)
- Supports multiple line items with GTIN, article numbers, discounts, and VAT rates
- Handles header-level discounts, shipping charges, and payment terms (Skonto)
- Validates the XML against the ZUGFeRD Extended XSD schema before embedding
- Outputs both a downloadable ZUGFeRD PDF and a standalone XML file

## Installation

```bash
pip install -r requirements.txt
```

## Running

```bash
streamlit run app.py
```

## UI Tabs

| Tab | Description |
|-----|-------------|
| **📄 Belegkopf & Käufer** | Invoice header, buyer details, optional ship-to address, document reference numbers |
| **🛒 Positionen** | Line items: article name, GTIN, article number, quantity, unit, gross price, discount %, VAT rate |
| **💰 Konditionen & Download** | Header discount, shipping charge, payment terms, Skonto, live totals preview, PDF generation & download |

The seller data (SHEKO GmbH) is configured in the sidebar.

## ZUGFeRD Compliance

- **Profile:** `urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended`
- **XSD validation:** performed via the `facturx` library (`check_xsd=True`) before embedding
- **Output format:** PDF/A-3 with embedded `factur-x.xml` attachment
- **Standard:** EN 16931 / ZUGFeRD 2.3 Extended / Factur-X 1.0

## Downloaded files

| File | Description |
|------|-------------|
| `Rechnung_<number>.pdf` | Hybrid ZUGFeRD PDF with embedded `factur-x.xml` — submit this to Markant/EDI systems |
| `factur-x.xml` | Standalone CII XML for archiving or separate processing |
