from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _safe_text(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return escape(text) if text else "-"


def _p(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_safe_text(value), style)


def _money(value: Any, currency: str = "EUR") -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
        return f"{amount:,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (InvalidOperation, TypeError, ValueError):
        return f"- {currency}"


def _num(value: Any) -> str:
    try:
        return str(Decimal(str(value)).normalize()).replace(".", ",")
    except (InvalidOperation, TypeError, ValueError):
        return "-"


def _date(value: str | None) -> str:
    if not value:
        return "-"
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return value


def _party_block(party: dict[str, Any]) -> str:
    lines = [
        party.get("name"),
        party.get("street"),
        " ".join(filter(None, [party.get("postal_code"), party.get("city")])),
        party.get("country"),
        f"USt-ID: {party.get('vat_id')}" if party.get("vat_id") else None,
    ]
    return "<br/>".join(escape(str(line)) for line in lines if line) or "-"


def _bank_block(bank: dict[str, Any]) -> str:
    lines = [
        f"IBAN: {bank.get('iban')}" if bank.get("iban") else None,
        f"BIC: {bank.get('bic')}" if bank.get("bic") else None,
        f"Kontoinhaber: {bank.get('account_name')}" if bank.get("account_name") else None,
        f"Zahlart-Code: {bank.get('payment_means_type_code')}" if bank.get("payment_means_type_code") else None,
    ]
    return "<br/>".join(escape(str(line)) for line in lines if line)


def _build_styles() -> dict[str, ParagraphStyle]:
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="InvoiceTitle",
        parent=styles["Title"],
        fontSize=22,
        leading=26,
        alignment=TA_RIGHT,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="SubtitleRight",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#475467"),
    ))
    styles.add(ParagraphStyle(
        name="SmallMuted",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#666666"),
    ))
    styles.add(ParagraphStyle(
        name="Section",
        parent=styles["Heading2"],
        fontSize=11,
        leading=14,
        spaceBefore=8,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="Cell",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=10.5,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="CellRight",
        parent=styles["Cell"],
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        name="HeaderCell",
        parent=styles["Cell"],
        textColor=colors.white,
        fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="LabelCell",
        parent=styles["Cell"],
        fontName="Helvetica-Bold",
    ))
    return styles


def render_invoice_pdf(invoice: dict[str, Any]) -> bytes:
    """Render a readable invoice PDF. Factur-X embedding happens in zugferd_builder.py."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Rechnung {invoice.get('invoice_number') or ''}",
        author="Invoice Converter",
        subject="Factur-X/XRechnung-XML",
    )

    styles = _build_styles()
    currency = invoice.get("currency") or "EUR"
    seller = invoice.get("seller") or {}
    buyer = invoice.get("buyer") or {}
    totals = invoice.get("totals") or {}
    line_items = invoice.get("line_items") or []
    tax_breakdown = invoice.get("tax_breakdown") or []
    bank = invoice.get("bank_details") or {}

    story: list[Any] = []

    # Neutral header: no LENCO reference and no "menschenlesbar" wording.
    header_table = Table(
        [
            [
                Paragraph("", styles["Normal"]),
                Paragraph("RECHNUNG", styles["InvoiceTitle"]),
            ],
            [
                Paragraph("", styles["Normal"]),
                Paragraph("Factur-X/XRechnung-XML", styles["SubtitleRight"]),
            ],
        ],
        colWidths=[84 * mm, 84 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, 1), 0.7, colors.HexColor("#111111")),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 10))

    party_table = Table(
        [
            [
                Paragraph("<b>Verkäufer</b><br/>" + _party_block(seller), styles["Cell"]),
                Paragraph("<b>Käufer</b><br/>" + _party_block(buyer), styles["Cell"]),
            ]
        ],
        colWidths=[84 * mm, 84 * mm],
        splitByRow=True,
    )
    party_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FCFCFD")),
        ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(party_table)
    story.append(Spacer(1, 10))

    # Full-width metadata table prevents overlaps for long labels and references.
    meta_rows = [
        ["Rechnungsnummer", invoice.get("invoice_number") or "-"],
        ["Rechnungsdatum", _date(invoice.get("issue_date"))],
        ["Fälligkeitsdatum", _date(invoice.get("due_date"))],
        ["Währung", currency],
        ["Buyer Reference / Leitweg-ID", invoice.get("buyer_reference") or "-"],
        ["Zahlungsreferenz", invoice.get("payment_reference") or "-"],
    ]
    meta_table = Table(
        [[_p(label, styles["LabelCell"]), _p(value, styles["Cell"])] for label, value in meta_rows],
        colWidths=[56 * mm, 112 * mm],
        splitByRow=True,
    )
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("Positionen", styles["Section"]))

    position_headers = ["#", "Beschreibung", "Menge", "Einzelpreis", "USt.-Kat.", "USt.", "Netto"]
    rows: list[list[Any]] = [[_p(header, styles["HeaderCell"]) for header in position_headers]]

    for idx, item in enumerate(line_items, start=1):
        rows.append([
            _p(str(idx), styles["CellRight"]),
            _p(item.get("description") or "-", styles["Cell"]),
            _p(f"{_num(item.get('quantity'))} {item.get('unit_code') or 'C62'}", styles["CellRight"]),
            _p(_money(item.get("unit_price"), currency), styles["CellRight"]),
            _p(item.get("vat_category") or "-", styles["CellRight"]),
            _p(f"{_num(item.get('vat_rate'))} %", styles["CellRight"]),
            _p(_money(item.get("net_amount"), currency), styles["CellRight"]),
        ])

    positions = LongTable(
        rows,
        colWidths=[8 * mm, 61 * mm, 19 * mm, 27 * mm, 16 * mm, 15 * mm, 22 * mm],
        repeatRows=1,
        splitByRow=True,
    )
    positions.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 4.5),
    ]))
    story.append(positions)
    story.append(Spacer(1, 12))

    if tax_breakdown:
        story.append(Paragraph("Steueraufschlüsselung", styles["Section"]))
        tax_rows: list[list[Any]] = [[_p(h, styles["LabelCell"]) for h in ["Kategorie", "Satz", "Bemessungsgrundlage", "Steuerbetrag"]]]
        for tax in tax_breakdown:
            tax_rows.append([
                _p(tax.get("category_code") or "-", styles["Cell"]),
                _p(f"{_num(tax.get('rate'))} %", styles["CellRight"]),
                _p(_money(tax.get("basis_amount"), currency), styles["CellRight"]),
                _p(_money(tax.get("tax_amount"), currency), styles["CellRight"]),
            ])
        tax_table = Table(tax_rows, colWidths=[31 * mm, 27 * mm, 57 * mm, 53 * mm], splitByRow=True)
        tax_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(tax_table)
        story.append(Spacer(1, 12))

    totals_rows = [
        ["Nettosumme", _money(totals.get("net_total"), currency)],
        ["Umsatzsteuer", _money(totals.get("tax_total"), currency)],
        ["Bruttosumme", _money(totals.get("gross_total"), currency)],
        ["Fälliger Betrag", _money(totals.get("due_amount") or totals.get("gross_total"), currency)],
    ]
    totals_table = Table(
        [[_p(label, styles["LabelCell"]), _p(value, styles["CellRight"])] for label, value in totals_rows],
        colWidths=[48 * mm, 43 * mm],
        hAlign="RIGHT",
        splitByRow=True,
    )
    totals_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F9FAFB")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D5DD")),
        ("FONTNAME", (0, 2), (-1, -1), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(totals_table)

    payment_parts: list[str] = []
    if invoice.get("payment_terms"):
        payment_parts.append(escape(str(invoice["payment_terms"])))
    if _bank_block(bank):
        payment_parts.append(_bank_block(bank))

    if payment_parts:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Zahlungsinformationen", styles["Section"]))
        story.append(Paragraph("<br/>".join(payment_parts), styles["Cell"]))

    notes = invoice.get("notes") or []
    if notes:
        story.append(Spacer(1, 12))
        story.append(Paragraph("Hinweise", styles["Section"]))
        for note in notes:
            story.append(Paragraph(_safe_text(note), styles["Cell"]))

    # No final explanatory sentence. The subtitle already states Factur-X/XRechnung-XML.
    doc.build(story)
    return buffer.getvalue()
