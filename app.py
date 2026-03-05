#!/usr/bin/env python3
"""
ZUGFeRD Extended Warenrechnungs-Generator – SHEKO GmbH
Erstellt konforme hybride PDF/XML-Rechnungen (ZUGFeRD 2.3 Extended / Factur-X)
"""

import streamlit as st
import io, os, tempfile
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from lxml import etree
import facturx

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT

# ─── Page setup ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ZUGFeRD Rechnungsgenerator – SHEKO",
    page_icon="🧾",
    layout="wide",
)

st.markdown("""
<style>
.stNumberInput input { font-size: 0.9rem; }
.stTextInput input   { font-size: 0.9rem; }
div[data-testid="stExpander"] { border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

st.title("🧾 ZUGFeRD Extended Warenrechnung")
st.caption("Erstellt konforme hybride PDF/XML-Rechnungen · ZUGFeRD 2.3 Extended (Factur-X)")

# ─── Session state ────────────────────────────────────────────────────────────
if "positions" not in st.session_state:
    st.session_state.positions = [
        {
            "gtin": "", "seller_id": "", "buyer_id": "", "name": "",
            "qty": 1.0, "unit": "H87",
            "gross_price": 0.0, "discount_pct": 0.0, "vat_rate": 19.0,
        }
    ]

# ─── Constants ────────────────────────────────────────────────────────────────
UNIT_OPTIONS = {
    "H87 – Stück":    "H87",
    "KGM – Kilogramm": "KGM",
    "GRM – Gramm":    "GRM",
    "LTR – Liter":    "LTR",
    "MTR – Meter":    "MTR",
    "XCT – Karton":   "XCT",
    "XBC – Kiste":    "XBC",
    "XPX – Palette":  "XPX",
    "PK  – Paket":    "PK",
}
VAT_RATES = [19.0, 7.0, 0.0]

CII_NS   = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM_NS   = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT_NS   = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
QDT_NS   = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
XS_NS    = "http://www.w3.org/2001/XMLSchema"

PROFILE_EXTENDED = "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended"

# ─── Helper: format dates ─────────────────────────────────────────────────────
def fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")

def fmt_money(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def fmt_qty(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

def fmt_price(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

def d2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def d4(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

# ─── Calculation engine ───────────────────────────────────────────────────────
def calculate_totals(positions, header_discount_pct, shipping_charge_eur, shipping_vat_rate=19.0):
    """Calculate invoice totals grouped by VAT rate."""
    vat_groups: dict[Decimal, Decimal] = {}

    for pos in positions:
        rate      = Decimal(str(pos["vat_rate"]))
        net_price = d4(pos["gross_price"]) * (1 - Decimal(str(pos["discount_pct"])) / 100)
        line_tot  = d2(net_price * Decimal(str(pos["qty"])))
        vat_groups[rate] = vat_groups.get(rate, Decimal("0")) + line_tot

    total_lines = sum(vat_groups.values())
    disc_pct    = Decimal(str(header_discount_pct)) / 100

    tax_details = []
    for rate, line_total in sorted(vat_groups.items(), reverse=True):
        allowance   = d2(line_total * disc_pct) if total_lines > 0 else Decimal("0")
        basis       = line_total - allowance
        vat_amount  = d2(basis * rate / 100)
        tax_details.append({
            "rate":       rate,
            "line_total": line_total,
            "allowance":  allowance,
            "charge":     Decimal("0"),
            "basis":      basis,
            "vat_amount": vat_amount,
        })

    # Add shipping charge
    if shipping_charge_eur > 0:
        ship_rate   = Decimal(str(shipping_vat_rate))
        ship_amount = d2(shipping_charge_eur)
        ship_vat    = d2(ship_amount * ship_rate / 100)
        for td in tax_details:
            if td["rate"] == ship_rate:
                td["charge"] += ship_amount
                td["basis"]  += ship_amount
                td["vat_amount"] = d2(td["basis"] * td["rate"] / 100)
                break
        else:
            tax_details.append({
                "rate":       ship_rate,
                "line_total": Decimal("0"),
                "allowance":  Decimal("0"),
                "charge":     ship_amount,
                "basis":      ship_amount,
                "vat_amount": ship_vat,
            })

    total_allowances  = sum(td["allowance"] for td in tax_details)
    total_charges     = d2(shipping_charge_eur) if shipping_charge_eur > 0 else Decimal("0")
    tax_basis_total   = total_lines + total_charges - total_allowances
    total_vat         = sum(td["vat_amount"] for td in tax_details)
    grand_total       = tax_basis_total + total_vat

    return {
        "tax_details":          tax_details,
        "line_total_amount":    total_lines,
        "charge_total_amount":  total_charges,
        "allowance_total_amount": total_allowances,
        "tax_basis_total":      tax_basis_total,
        "total_vat":            total_vat,
        "grand_total":          grand_total,
    }

# ─── XML Builder ──────────────────────────────────────────────────────────────
def build_xml(data: dict) -> bytes:
    """Build a ZUGFeRD Extended CII XML from invoice data dict."""
    nsmap = {
        "rsm": CII_NS,
        "ram": RAM_NS,
        "udt": UDT_NS,
        "qdt": QDT_NS,
        "xs":  XS_NS,
    }

    def rsm(tag):  return f"{{{CII_NS}}}{tag}"
    def ram(tag):  return f"{{{RAM_NS}}}{tag}"
    def udt(tag):  return f"{{{UDT_NS}}}{tag}"

    root = etree.Element(rsm("CrossIndustryInvoice"), nsmap=nsmap)

    # ── ExchangedDocumentContext ──────────────────────────────────────────────
    ctx = etree.SubElement(root, rsm("ExchangedDocumentContext"))
    if data.get("test_mode"):
        ti = etree.SubElement(ctx, ram("TestIndicator"))
        etree.SubElement(ti, udt("Indicator")).text = "true"
    gsdcp = etree.SubElement(ctx, ram("GuidelineSpecifiedDocumentContextParameter"))
    etree.SubElement(gsdcp, ram("ID")).text = PROFILE_EXTENDED

    # ── ExchangedDocument ─────────────────────────────────────────────────────
    doc = etree.SubElement(root, rsm("ExchangedDocument"))
    etree.SubElement(doc, ram("ID")).text       = data["inv_number"]
    etree.SubElement(doc, ram("Name")).text     = data["doc_type"]
    etree.SubElement(doc, ram("TypeCode")).text = "380"
    idt = etree.SubElement(doc, ram("IssueDateTime"))
    etree.SubElement(idt, udt("DateTimeString"), format="102").text = fmt_date(data["inv_date"])

    def add_note(parent, content, subject_code=None, content_code=None):
        note = etree.SubElement(parent, ram("IncludedNote"))
        if content_code:
            etree.SubElement(note, ram("ContentCode")).text = content_code
        etree.SubElement(note, ram("Content")).text = content
        if subject_code:
            etree.SubElement(note, ram("SubjectCode")).text = subject_code

    if data.get("entgeltminderung"):
        add_note(doc, "Es bestehen Rabatt- oder Bonusvereinbarungen.", "AAK", "ST3")
    if data.get("seller_reg_note"):
        add_note(doc, data["seller_reg_note"], "REG")
    if data.get("payment_note"):
        add_note(doc, data["payment_note"])

    # ── SupplyChainTradeTransaction ───────────────────────────────────────────
    sctt = etree.SubElement(root, rsm("SupplyChainTradeTransaction"))

    for i, pos in enumerate(data["positions"], 1):
        line = etree.SubElement(sctt, ram("IncludedSupplyChainTradeLineItem"))
        aldoc = etree.SubElement(line, ram("AssociatedDocumentLineDocument"))
        etree.SubElement(aldoc, ram("LineID")).text = str(i)

        prod = etree.SubElement(line, ram("SpecifiedTradeProduct"))
        if pos.get("gtin"):
            etree.SubElement(prod, ram("GlobalID"), schemeID="0160").text = pos["gtin"]
        if pos.get("seller_id"):
            etree.SubElement(prod, ram("SellerAssignedID")).text = pos["seller_id"]
        if pos.get("buyer_id"):
            etree.SubElement(prod, ram("BuyerAssignedID")).text  = pos["buyer_id"]
        etree.SubElement(prod, ram("Name")).text = pos["name"]

        agree = etree.SubElement(line, ram("SpecifiedLineTradeAgreement"))
        gross = etree.SubElement(agree, ram("GrossPriceProductTradePrice"))
        etree.SubElement(gross, ram("ChargeAmount")).text = fmt_price(d4(pos["gross_price"]))
        if pos.get("discount_pct", 0) > 0:
            disc_alloc = etree.SubElement(gross, ram("AppliedTradeAllowanceCharge"))
            ci = etree.SubElement(disc_alloc, ram("ChargeIndicator"))
            etree.SubElement(ci, udt("Indicator")).text = "false"
            disc_amount = d4(d4(pos["gross_price"]) * Decimal(str(pos["discount_pct"])) / 100)
            etree.SubElement(disc_alloc, ram("ActualAmount")).text = fmt_price(disc_amount)
            etree.SubElement(disc_alloc, ram("Reason")).text = "Artikelrabatt"

        net_price = d4(pos["gross_price"]) * (1 - Decimal(str(pos.get("discount_pct", 0))) / 100)
        net_elem  = etree.SubElement(agree, ram("NetPriceProductTradePrice"))
        etree.SubElement(net_elem, ram("ChargeAmount")).text = fmt_price(net_price)

        deliv = etree.SubElement(line, ram("SpecifiedLineTradeDelivery"))
        etree.SubElement(deliv, ram("BilledQuantity"), unitCode=pos["unit"]).text = fmt_qty(Decimal(str(pos["qty"])))

        settl = etree.SubElement(line, ram("SpecifiedLineTradeSettlement"))
        tax   = etree.SubElement(settl, ram("ApplicableTradeTax"))
        etree.SubElement(tax, ram("TypeCode")).text              = "VAT"
        etree.SubElement(tax, ram("CategoryCode")).text          = "S" if pos["vat_rate"] > 0 else "Z"
        etree.SubElement(tax, ram("RateApplicablePercent")).text = str(pos["vat_rate"])

        line_total = d2(net_price * Decimal(str(pos["qty"])))
        summ = etree.SubElement(settl, ram("SpecifiedTradeSettlementLineMonetarySummation"))
        etree.SubElement(summ, ram("LineTotalAmount")).text = fmt_money(line_total)

    # ── Header trade agreement ────────────────────────────────────────────────
    hta = etree.SubElement(sctt, ram("ApplicableHeaderTradeAgreement"))

    def add_party(parent, tag, party):
        p = etree.SubElement(parent, ram(tag))
        if party.get("id"):
            etree.SubElement(p, ram("ID")).text = party["id"]
        if party.get("gln"):
            etree.SubElement(p, ram("GlobalID"), schemeID="0088").text = party["gln"]
        etree.SubElement(p, ram("Name")).text = party["name"]
        if party.get("phone") or party.get("email"):
            contact = etree.SubElement(p, ram("DefinedTradeContact"))
            if party.get("phone"):
                tel = etree.SubElement(contact, ram("TelephoneUniversalCommunication"))
                etree.SubElement(tel, ram("CompleteNumber")).text = party["phone"]
            if party.get("email"):
                mail = etree.SubElement(contact, ram("EmailURIUniversalCommunication"))
                etree.SubElement(mail, ram("URIID")).text = party["email"]
        addr = etree.SubElement(p, ram("PostalTradeAddress"))
        if party.get("zip"):
            etree.SubElement(addr, ram("PostcodeCode")).text = party["zip"]
        if party.get("street"):
            etree.SubElement(addr, ram("LineOne")).text = party["street"]
        if party.get("city"):
            etree.SubElement(addr, ram("CityName")).text = party["city"]
        etree.SubElement(addr, ram("CountryID")).text = party.get("country", "DE")
        if party.get("vat_id"):
            taxreg = etree.SubElement(p, ram("SpecifiedTaxRegistration"))
            etree.SubElement(taxreg, ram("ID"), schemeID="VA").text = party["vat_id"]
        return p

    add_party(hta, "SellerTradeParty", data["seller"])
    add_party(hta, "BuyerTradeParty",  data["buyer"])

    if data.get("seller_order_ref"):
        sord = etree.SubElement(hta, ram("SellerOrderReferencedDocument"))
        etree.SubElement(sord, ram("IssuerAssignedID")).text = data["seller_order_ref"]
    if data.get("order_ref"):
        bord = etree.SubElement(hta, ram("BuyerOrderReferencedDocument"))
        etree.SubElement(bord, ram("IssuerAssignedID")).text = data["order_ref"]

    # ── Header trade delivery ─────────────────────────────────────────────────
    htd = etree.SubElement(sctt, ram("ApplicableHeaderTradeDelivery"))
    shipto = data.get("shipto")
    if shipto and shipto.get("name"):
        sp = etree.SubElement(htd, ram("ShipToTradeParty"))
        if shipto.get("gln"):
            etree.SubElement(sp, ram("GlobalID"), schemeID="0088").text = shipto["gln"]
        etree.SubElement(sp, ram("Name")).text = shipto["name"]
        if shipto.get("dept"):
            dc = etree.SubElement(sp, ram("DefinedTradeContact"))
            etree.SubElement(dc, ram("DepartmentName")).text = shipto["dept"]
        addr = etree.SubElement(sp, ram("PostalTradeAddress"))
        if shipto.get("zip"):
            etree.SubElement(addr, ram("PostcodeCode")).text = shipto["zip"]
        if shipto.get("street"):
            etree.SubElement(addr, ram("LineOne")).text = shipto["street"]
        if shipto.get("city"):
            etree.SubElement(addr, ram("CityName")).text = shipto["city"]
        etree.SubElement(addr, ram("CountryID")).text = shipto.get("country", "DE")

    if data.get("delivery_date"):
        adsce = etree.SubElement(htd, ram("ActualDeliverySupplyChainEvent"))
        ocdt  = etree.SubElement(adsce, ram("OccurrenceDateTime"))
        etree.SubElement(ocdt, udt("DateTimeString"), format="102").text = fmt_date(data["delivery_date"])

    if data.get("delivery_ref"):
        dnrd = etree.SubElement(htd, ram("DeliveryNoteReferencedDocument"))
        etree.SubElement(dnrd, ram("IssuerAssignedID")).text = data["delivery_ref"]

    # ── Header trade settlement ───────────────────────────────────────────────
    hts = etree.SubElement(sctt, ram("ApplicableHeaderTradeSettlement"))
    etree.SubElement(hts, ram("InvoiceCurrencyCode")).text = data.get("currency", "EUR")

    totals = data["totals"]

    for td in totals["tax_details"]:
        appt = etree.SubElement(hts, ram("ApplicableTradeTax"))
        etree.SubElement(appt, ram("CalculatedAmount")).text       = fmt_money(td["vat_amount"])
        etree.SubElement(appt, ram("TypeCode")).text               = "VAT"
        etree.SubElement(appt, ram("BasisAmount")).text            = fmt_money(td["basis"])
        etree.SubElement(appt, ram("LineTotalBasisAmount")).text   = fmt_money(td["line_total"])
        etree.SubElement(appt, ram("AllowanceChargeBasisAmount")).text = fmt_money(-td["allowance"])
        etree.SubElement(appt, ram("CategoryCode")).text           = "S" if td["rate"] > 0 else "Z"
        etree.SubElement(appt, ram("RateApplicablePercent")).text  = str(td["rate"])

    for td in totals["tax_details"]:
        if td["allowance"] > 0:
            alloc = etree.SubElement(hts, ram("SpecifiedTradeAllowanceCharge"))
            ci = etree.SubElement(alloc, ram("ChargeIndicator"))
            etree.SubElement(ci, udt("Indicator")).text = "false"
            if data.get("header_discount_pct"):
                etree.SubElement(alloc, ram("CalculationPercent")).text = str(data["header_discount_pct"])
            etree.SubElement(alloc, ram("BasisAmount")).text  = fmt_money(td["line_total"])
            etree.SubElement(alloc, ram("ActualAmount")).text = fmt_money(td["allowance"])
            etree.SubElement(alloc, ram("Reason")).text       = data.get("header_discount_name", "Rechnungsrabatt")
            ctax = etree.SubElement(alloc, ram("CategoryTradeTax"))
            etree.SubElement(ctax, ram("TypeCode")).text             = "VAT"
            etree.SubElement(ctax, ram("CategoryCode")).text         = "S" if td["rate"] > 0 else "Z"
            etree.SubElement(ctax, ram("RateApplicablePercent")).text = str(td["rate"])

    if data.get("shipping_charge_eur", 0) > 0:
        ship_rate = Decimal(str(data.get("shipping_vat_rate", 19.0)))
        slsc = etree.SubElement(hts, ram("SpecifiedLogisticsServiceCharge"))
        etree.SubElement(slsc, ram("Description")).text  = "Transportkosten"
        etree.SubElement(slsc, ram("AppliedAmount")).text = fmt_money(d2(data["shipping_charge_eur"]))
        atax = etree.SubElement(slsc, ram("AppliedTradeTax"))
        etree.SubElement(atax, ram("TypeCode")).text              = "VAT"
        etree.SubElement(atax, ram("CategoryCode")).text          = "S" if ship_rate > 0 else "Z"
        etree.SubElement(atax, ram("RateApplicablePercent")).text = str(ship_rate)

    if data.get("payment_note") or data.get("skonto_pct", 0) > 0:
        pt = etree.SubElement(hts, ram("SpecifiedTradePaymentTerms"))
        if data.get("payment_note"):
            etree.SubElement(pt, ram("Description")).text = data["payment_note"]
        if data.get("skonto_pct", 0) > 0:
            apt = etree.SubElement(pt, ram("ApplicableTradePaymentDiscountTerms"))
            etree.SubElement(apt, ram("BasisPeriodMeasure"), unitCode="DAY").text = str(data.get("skonto_days", 14))
            etree.SubElement(apt, ram("CalculationPercent")).text = str(data["skonto_pct"])

    summ = etree.SubElement(hts, ram("SpecifiedTradeSettlementHeaderMonetarySummation"))
    etree.SubElement(summ, ram("LineTotalAmount")).text       = fmt_money(totals["line_total_amount"])
    etree.SubElement(summ, ram("ChargeTotalAmount")).text     = fmt_money(totals["charge_total_amount"])
    etree.SubElement(summ, ram("AllowanceTotalAmount")).text  = fmt_money(totals["allowance_total_amount"])
    etree.SubElement(summ, ram("TaxBasisTotalAmount")).text   = fmt_money(totals["tax_basis_total"])
    etree.SubElement(summ, ram("TaxTotalAmount"), currencyID=data.get("currency", "EUR")).text = fmt_money(totals["total_vat"])
    etree.SubElement(summ, ram("GrandTotalAmount")).text      = fmt_money(totals["grand_total"])
    etree.SubElement(summ, ram("TotalPrepaidAmount")).text    = "0.00"
    etree.SubElement(summ, ram("DuePayableAmount")).text      = fmt_money(totals["grand_total"])

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# ─── PDF Builder ──────────────────────────────────────────────────────────────
SHEKO_BLUE  = colors.HexColor("#003D8F")
LIGHT_GRAY  = colors.HexColor("#F5F5F5")
MID_GRAY    = colors.HexColor("#CCCCCC")
DARK_GRAY   = colors.HexColor("#555555")

def build_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20*mm, rightMargin=15*mm,
        topMargin=15*mm, bottomMargin=25*mm,
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    normal.fontName = "Helvetica"
    normal.fontSize = 9

    def style(name, **kw):
        s = ParagraphStyle(name, parent=normal, **kw)
        return s

    bold9   = style("bold9",   fontName="Helvetica-Bold",  fontSize=9)
    small8  = style("small8",  fontSize=8,  textColor=DARK_GRAY)
    right9  = style("right9",  alignment=TA_RIGHT, fontSize=9)
    bold_r  = style("bold_r",  fontName="Helvetica-Bold", alignment=TA_RIGHT, fontSize=9)
    title_s = style("title_s", fontName="Helvetica-Bold", fontSize=15, textColor=SHEKO_BLUE)
    head10  = style("head10",  fontName="Helvetica-Bold", fontSize=10)

    seller = data["seller"]
    buyer  = data["buyer"]
    totals = data["totals"]

    story = []

    header_data = [
        [
            Paragraph(f"<b><font color='#003D8F'>{seller['name']}</font></b>", title_s),
            Paragraph(
                f"<font size=7 color='#555555'>"
                f"{seller.get('street','')}, {seller.get('zip','')} {seller.get('city','')}<br/>"
                f"USt-IdNr.: {seller.get('vat_id','')}<br/>"
                f"{seller.get('email','')}"
                f"</font>",
                style("hdr_addr", fontSize=7, textColor=DARK_GRAY, alignment=TA_RIGHT)
            )
        ]
    ]
    hdr_table = Table(header_data, colWidths=[95*mm, 80*mm])
    hdr_table.setStyle(TableStyle([
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 0),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story.append(hdr_table)
    story.append(HRFlowable(width="100%", thickness=1.5, color=SHEKO_BLUE, spaceAfter=6))

    shipto = data.get("shipto") or {}
    addr_cols = [
        Paragraph(
            f"<b>Rechnungsempfänger</b><br/>"
            f"{buyer['name']}<br/>"
            f"{buyer.get('street','')}<br/>"
            f"{buyer.get('zip','')} {buyer.get('city','')}<br/>"
            f"{'GLN: ' + buyer['gln'] if buyer.get('gln') else ''}",
            style("addr", fontSize=9)
        ),
    ]
    if shipto and shipto.get("name") and shipto.get("name") != buyer.get("name"):
        addr_cols.append(
            Paragraph(
                f"<b>Warenempfänger</b><br/>"
                f"{shipto['name']}<br/>"
                f"{shipto.get('street','')}<br/>"
                f"{shipto.get('zip','')} {shipto.get('city','')}<br/>"
                f"{'GLN: ' + shipto['gln'] if shipto.get('gln') else ''}",
                style("shipto", fontSize=9)
            )
        )
        addr_table = Table([addr_cols], colWidths=[87*mm, 87*mm])
    else:
        addr_table = Table([[addr_cols[0], ""]], colWidths=[87*mm, 87*mm])

    addr_table.setStyle(TableStyle([
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(addr_table)

    inv_meta = [
        ["Rechnungsnummer:",  data["inv_number"],
         "Rechnungsdatum:",   data["inv_date"].strftime("%d.%m.%Y")],
        ["Lieferdatum:",      data.get("delivery_date", data["inv_date"]).strftime("%d.%m.%Y"),
         "Belegtyp:",         data["doc_type"]],
    ]
    if data.get("order_ref"):
        inv_meta.append(["Bestellnummer:", data["order_ref"], "", ""])
    if data.get("delivery_ref"):
        inv_meta.append(["Lieferschein:", data["delivery_ref"], "", ""])
    if data.get("seller_order_ref"):
        inv_meta.append(["Auftragsnummer:", data["seller_order_ref"], "", ""])

    meta_tbl = Table(inv_meta, colWidths=[40*mm, 50*mm, 40*mm, 45*mm])
    meta_tbl.setStyle(TableStyle([
        ("FONTNAME",  (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",  (2,0), (2,-1), "Helvetica-Bold"),
        ("FONTSIZE",  (0,0), (-1,-1), 8.5),
        ("TOPPADDING",    (0,0), (-1,-1), 1),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#F5F5F5"), colors.white]),
    ]))
    story.append(meta_tbl)
    story.append(Spacer(1, 6))

    story.append(Paragraph(f"<b>{data['doc_type']}</b> Nr. {data['inv_number']}", head10))
    story.append(Spacer(1, 4))

    col_headers = ["Pos.", "GTIN / Art.-Nr.", "Bezeichnung", "Menge", "Einheit",
                   "Bruttopreis", "Rabatt", "Nettopreis", "MwSt.", "Betrag (€)"]
    col_w = [8*mm, 25*mm, 47*mm, 12*mm, 10*mm, 16*mm, 11*mm, 16*mm, 9*mm, 16*mm]

    rows = [col_headers]
    for i, pos in enumerate(data["positions"], 1):
        net_price = d4(pos["gross_price"]) * (1 - Decimal(str(pos.get("discount_pct", 0))) / 100)
        line_total = d2(net_price * Decimal(str(pos["qty"])))
        disc_str   = f"{pos.get('discount_pct',0):.1f}%" if pos.get("discount_pct", 0) > 0 else "–"
        gtin_str   = pos.get("gtin", "")
        sid_str    = pos.get("seller_id", "")
        art_str    = gtin_str if gtin_str else sid_str

        rows.append([
            str(i),
            art_str,
            pos["name"],
            f"{float(pos['qty']):.0f}" if pos['qty'] == int(pos['qty']) else f"{pos['qty']:.2f}",
            pos["unit"],
            f"{float(pos['gross_price']):.2f}",
            disc_str,
            f"{float(net_price):.4f}",
            f"{pos['vat_rate']:.0f}%",
            f"{float(line_total):.2f}",
        ])

    pos_tbl = Table(rows, colWidths=col_w, repeatRows=1)
    pos_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), SHEKO_BLUE),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 7.5),
        ("ALIGN",         (3,0), (-1,-1), "RIGHT"),
        ("ALIGN",         (0,0), (2,-1), "LEFT"),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#F5F5F5")]),
        ("GRID",          (0,0), (-1,-1), 0.3, MID_GRAY),
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]))
    story.append(pos_tbl)
    story.append(Spacer(1, 6))

    tot_rows = []
    tot_rows.append(["Warenwert (netto):", f"{float(totals['line_total_amount']):.2f} €"])
    if totals["charge_total_amount"] > 0:
        tot_rows.append(["Transportkosten:", f"{float(totals['charge_total_amount']):.2f} €"])
    if totals["allowance_total_amount"] > 0:
        tot_rows.append([f"Rechnungsrabatt ({data.get('header_discount_pct',0):.1f}%):",
                         f"– {float(totals['allowance_total_amount']):.2f} €"])
    tot_rows.append(["Nettobetrag:", f"{float(totals['tax_basis_total']):.2f} €"])
    for td in totals["tax_details"]:
        if td["vat_amount"] > 0:
            tot_rows.append([f"MwSt. {td['rate']:.0f}% auf {float(td['basis']):.2f} €:",
                             f"{float(td['vat_amount']):.2f} €"])
    tot_rows.append(["RECHNUNGSBETRAG:", f"{float(totals['grand_total']):.2f} €"])

    tot_tbl = Table(
        [[Paragraph(r, bold9 if r[0]=="R" else small8),
          Paragraph(v, bold_r if r[0]=="R" else right9)]
         for r, v in tot_rows],
        colWidths=[75*mm, 35*mm],
        hAlign="RIGHT"
    )
    tot_tbl.setStyle(TableStyle([
        ("TOPPADDING",    (0,0), (-1,-1), 2),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ("LINEABOVE",     (0,-1),(-1,-1), 1, SHEKO_BLUE),
        ("BACKGROUND",    (0,-1),(-1,-1), SHEKO_BLUE),
        ("TEXTCOLOR",     (0,-1),(-1,-1), colors.white),
    ]))
    story.append(tot_tbl)
    story.append(Spacer(1, 8))

    footer_parts = []
    if data.get("payment_note"):
        footer_parts.append(data["payment_note"])
    if data.get("skonto_pct", 0) > 0:
        footer_parts.append(f"Bei Zahlung innerhalb {data['skonto_days']} Tagen gewähren wir {data['skonto_pct']:.1f}% Skonto.")
    if data.get("entgeltminderung"):
        footer_parts.append("Es bestehen Rabatt- oder Bonusvereinbarungen.")
    if footer_parts:
        story.append(Paragraph(" | ".join(footer_parts), small8))
        story.append(Spacer(1, 4))

    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GRAY))
    story.append(Paragraph(
        f"<font size=7 color='#888888'>Diese Rechnung enthält ein eingebettetes ZUGFeRD Extended XML (factur-x.xml) · "
        f"Ausgestellt von {seller['name']} · {seller.get('street','')} · {seller.get('zip','')} {seller.get('city','')}</font>",
        style("foot", fontSize=7, textColor=colors.HexColor("#888888"))
    ))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🏢 Verkäufer (SHEKO)")
    seller_name   = st.text_input("Firmenname",        value="SHEKO GmbH")
    seller_street = st.text_input("Straße",            value="Große Elbstraße 39")
    seller_zip    = st.text_input("PLZ",               value="22767")
    seller_city   = st.text_input("Ort",               value="Hamburg")
    seller_gln    = st.text_input("GLN Verkäufer",     value="", placeholder="13-stellig")
    seller_id     = st.text_input("Lieferantennr. beim Käufer", value="")
    seller_vat    = st.text_input("USt-IdNr.",         value="", placeholder="DE123456789")
    seller_email  = st.text_input("E-Mail",            value="")
    seller_phone  = st.text_input("Telefon",           value="")
    st.divider()
    test_mode = st.checkbox("Testmodus (TestIndicator=true)", value=True)
    shipping_vat_rate = st.selectbox("MwSt. auf Transportkosten", [19.0, 7.0, 0.0], index=0)

tab1, tab2, tab3 = st.tabs(["📄 Belegkopf & Käufer", "🛒 Positionen", "💰 Konditionen & Download"])

with tab1:
    st.subheader("Käufer / Rechnungsempfänger")
    c1, c2, c3 = st.columns(3)
    buyer_name   = c1.text_input("Firmenname Käufer *", value="Markant Handels und Service GmbH")
    buyer_id     = c2.text_input("Kundennummer",        value="")
    buyer_gln    = c3.text_input("GLN Käufer",          value="", placeholder="13-stellig")
    c1, c2, c3 = st.columns(3)
    buyer_street = c1.text_input("Straße",  value="")
    buyer_zip    = c2.text_input("PLZ",     value="")
    buyer_city   = c3.text_input("Ort",     value="")

    st.subheader("Abweichende Lieferadresse")
    use_shipto = st.checkbox("Abweichende Lieferadresse / Warenempfänger angeben")
    if use_shipto:
        c1, c2, c3 = st.columns(3)
        shipto_name   = c1.text_input("Name Warenempfänger")
        shipto_gln    = c2.text_input("GLN Warenempfänger", placeholder="13-stellig")
        shipto_dept   = c3.text_input("Abteilung / ILN-Filiale")
        c1, c2, c3 = st.columns(3)
        shipto_street = c1.text_input("Straße (Lieferort)")
        shipto_zip    = c2.text_input("PLZ (Lieferort)")
        shipto_city   = c3.text_input("Ort (Lieferort)")
    else:
        shipto_name = buyer_name; shipto_gln = buyer_gln; shipto_dept = ""
        shipto_street = buyer_street; shipto_zip = buyer_zip; shipto_city = buyer_city

    st.subheader("Belegkopf")
    c1, c2, c3, c4 = st.columns(4)
    inv_number    = c1.text_input("Rechnungsnummer *", value="")
    inv_date      = c2.date_input("Rechnungsdatum *",  value=date.today())
    delivery_date = c3.date_input("Liefer-/Leistungsdatum", value=date.today())
    currency      = c4.selectbox("Währung", ["EUR"], index=0)

    c1, c2, c3 = st.columns(3)
    order_ref    = c1.text_input("Bestellnummer (Käufer)", value="")
    delivery_ref = c2.text_input("Lieferscheinnummer",     value="")
    seller_order = c3.text_input("Auftragsnummer (Verkäufer)", value="")

    doc_type = st.selectbox("Belegqualifizierung",
        ["WARENRECHNUNG", "SAMMELRECHNUNG", "SERVICERECHNUNG", "KOSTENRECHNUNG", "REPARATURRECHNUNG"])

with tab2:
    st.subheader("Rechnungspositionen")

    def add_pos():
        st.session_state.positions.append(
            {"gtin":"","seller_id":"","buyer_id":"","name":"",
             "qty":1.0,"unit":"H87","gross_price":0.0,"discount_pct":0.0,"vat_rate":19.0}
        )

    to_remove = []
    for i, pos in enumerate(st.session_state.positions):
        with st.container(border=True):
            cols = st.columns([0.05, 0.35, 0.15, 0.12, 0.33])
            cols[0].markdown(f"**{i+1}**")
            pos["name"]      = cols[1].text_input("Artikelbezeichnung *", value=pos["name"],      key=f"n{i}")
            pos["gtin"]      = cols[2].text_input("GTIN",                 value=pos["gtin"],      key=f"g{i}", placeholder="13-stellig")
            pos["seller_id"] = cols[3].text_input("Art.-Nr.",              value=pos.get("seller_id",""), key=f"s{i}")
            if cols[4].button("✕ entfernen", key=f"d{i}"):
                to_remove.append(i)

            c1, c2, c3, c4, c5 = st.columns(5)
            pos["qty"]         = c1.number_input("Menge *", value=float(pos["qty"]), min_value=0.0001, step=1.0, key=f"q{i}")
            unit_lbl           = c2.selectbox("Einheit", list(UNIT_OPTIONS.keys()),
                                     index=list(UNIT_OPTIONS.values()).index(pos["unit"])
                                     if pos["unit"] in UNIT_OPTIONS.values() else 0, key=f"u{i}")
            pos["unit"]        = UNIT_OPTIONS[unit_lbl]
            pos["gross_price"] = c3.number_input("Bruttopreis/Einh. (€)", value=float(pos["gross_price"]),
                                     min_value=0.0, step=0.01, format="%.4f", key=f"p{i}")
            pos["discount_pct"]= c4.number_input("Rabatt %", value=float(pos["discount_pct"]),
                                     min_value=0.0, max_value=100.0, step=0.5, key=f"r{i}")
            pos["vat_rate"]    = c5.selectbox("MwSt. %", VAT_RATES,
                                     index=VAT_RATES.index(pos["vat_rate"]) if pos["vat_rate"] in VAT_RATES else 0,
                                     key=f"v{i}")

            net  = d4(pos["gross_price"]) * (1 - Decimal(str(pos["discount_pct"])) / 100)
            tot  = d2(net * Decimal(str(pos["qty"])))
            st.caption(f"Nettopreis: {float(net):.4f} € | Positionsbetrag: {float(tot):.2f} € (netto)")

    for idx in reversed(to_remove):
        st.session_state.positions.pop(idx)

    st.button("➕ Position hinzufügen", on_click=add_pos)

with tab3:
    st.subheader("Konditionen")
    c1, c2, c3 = st.columns(3)
    header_disc_pct  = c1.number_input("Rechnungsrabatt (%)", value=0.0, min_value=0.0, max_value=100.0, step=0.5)
    header_disc_name = c2.text_input("Rabattbezeichnung", value="Rechnungsrabatt")
    shipping_charge  = c3.number_input("Transportkosten (€, netto)", value=0.0, min_value=0.0, step=0.50)

    c1, c2 = st.columns(2)
    skonto_pct  = c1.number_input("Skonto (%)", value=0.0, min_value=0.0, max_value=100.0, step=0.5)
    skonto_days = c2.number_input("Skonto-Frist (Tage)", value=14, min_value=1, step=1)
    payment_note = st.text_input("Zahlungsbedingung (Freitext)", value="Zahlbar innerhalb 30 Tagen netto.")
    entgeltminderung = st.checkbox("Hinweis auf Entgeltminderung (Rabatt-/Bonusvereinbarungen) anfügen")

    seller_reg = (
        f"{seller_name}\n{seller_street}\n{seller_zip} {seller_city}\n"
        f"USt-IdNr: {seller_vat}\n{seller_email}"
    ).strip()

    if st.session_state.positions:
        totals = calculate_totals(
            st.session_state.positions, header_disc_pct,
            shipping_charge, shipping_vat_rate
        )
        st.divider()
        st.subheader("Vorschau Beträge")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Warenwert netto",      f"{float(totals['line_total_amount']):.2f} €")
        c2.metric("Steuern gesamt",        f"{float(totals['total_vat']):.2f} €")
        c3.metric("Rechnungsbetrag brutto",f"{float(totals['grand_total']):.2f} €")
        c4.metric("Positionen",            len(st.session_state.positions))

        for td in totals["tax_details"]:
            st.caption(
                f"MwSt. {td['rate']:.0f}%: Basis {float(td['basis']):.2f} € → "
                f"Steuer {float(td['vat_amount']):.2f} €"
            )

    st.divider()
    generate = st.button("⚡ ZUGFeRD PDF erstellen", type="primary", use_container_width=True)

    if generate:
        errors = []
        if not inv_number.strip():
            errors.append("Rechnungsnummer fehlt.")
        if not buyer_name.strip():
            errors.append("Name Käufer fehlt.")
        if not seller_name.strip():
            errors.append("Name Verkäufer fehlt.")
        if not st.session_state.positions:
            errors.append("Mindestens eine Position erforderlich.")
        for i, pos in enumerate(st.session_state.positions, 1):
            if not pos["name"].strip():
                errors.append(f"Position {i}: Artikelbezeichnung fehlt.")
            if pos["gross_price"] <= 0 and pos.get("discount_pct", 0) < 100:
                errors.append(f"Position {i}: Preis muss > 0 sein (außer bei Nullpositionen).")

        if errors:
            for e in errors:
                st.error(e)
        else:
            with st.spinner("Generiere ZUGFeRD Extended Rechnung …"):
                invoice_data = {
                    "inv_number":      inv_number.strip(),
                    "inv_date":        inv_date,
                    "delivery_date":   delivery_date,
                    "currency":        currency,
                    "doc_type":        doc_type,
                    "order_ref":       order_ref.strip(),
                    "delivery_ref":    delivery_ref.strip(),
                    "seller_order_ref":seller_order.strip(),
                    "test_mode":       test_mode,
                    "seller": {
                        "name":    seller_name,
                        "id":      seller_id,
                        "gln":     seller_gln.strip(),
                        "street":  seller_street,
                        "zip":     seller_zip,
                        "city":    seller_city,
                        "country": "DE",
                        "vat_id":  seller_vat.strip(),
                        "email":   seller_email.strip(),
                        "phone":   seller_phone.strip(),
                    },
                    "buyer": {
                        "name":    buyer_name.strip(),
                        "id":      buyer_id.strip(),
                        "gln":     buyer_gln.strip(),
                        "street":  buyer_street.strip(),
                        "zip":     buyer_zip.strip(),
                        "city":    buyer_city.strip(),
                        "country": "DE",
                    },
                    "shipto": {
                        "name":    shipto_name,
                        "gln":     shipto_gln,
                        "dept":    shipto_dept,
                        "street":  shipto_street,
                        "zip":     shipto_zip,
                        "city":    shipto_city,
                        "country": "DE",
                    },
                    "positions":           [dict(p) for p in st.session_state.positions],
                    "header_discount_pct": header_disc_pct,
                    "header_discount_name":header_disc_name,
                    "shipping_charge_eur": shipping_charge,
                    "shipping_vat_rate":   shipping_vat_rate,
                    "skonto_pct":          skonto_pct,
                    "skonto_days":         int(skonto_days),
                    "payment_note":        payment_note.strip(),
                    "entgeltminderung":    entgeltminderung,
                    "seller_reg_note":     seller_reg,
                    "totals":             calculate_totals(
                        st.session_state.positions, header_disc_pct,
                        shipping_charge, shipping_vat_rate
                    ),
                }

                try:
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
                            zugferd_bytes = f.read()

                    filename = f"Rechnung_{inv_number.strip().replace('/','_')}.pdf"
                    st.success("✅ ZUGFeRD Extended Rechnung erfolgreich erstellt!")
                    col1, col2 = st.columns(2)
                    col1.download_button(
                        label="⬇️ ZUGFeRD PDF herunterladen",
                        data=zugferd_bytes,
                        file_name=filename,
                        mime="application/pdf",
                        type="primary",
                    )
                    col2.download_button(
                        label="⬇️ XML-Datei (factur-x.xml)",
                        data=xml_bytes,
                        file_name="factur-x.xml",
                        mime="application/xml",
                    )

                    with st.expander("📋 XML-Vorschau (factur-x.xml)"):
                        st.code(xml_bytes.decode("utf-8"), language="xml")

                except Exception as e:
                    st.error(f"Fehler bei der PDF-Generierung: {e}")
                    st.exception(e)
                    try:
                        xml_bytes_fallback = build_xml(invoice_data)
                        st.warning("XML konnte trotzdem generiert werden:")
                        st.download_button("⬇️ XML herunterladen (Debug)", xml_bytes_fallback,
                                          "factur-x.xml", "application/xml")
                    except Exception as e2:
                        st.error(f"Auch XML-Generierung fehlgeschlagen: {e2}")
