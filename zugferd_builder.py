from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from lxml import etree


class FacturXBuildError(Exception):
    """Raised when Factur-X XML or PDF embedding fails."""


NSMAP = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "qdt": "urn:un:unece:uncefact:data:standard:QualifiedDataType:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

RSM = f"{{{NSMAP['rsm']}}}"
RAM = f"{{{NSMAP['ram']}}}"
UDT = f"{{{NSMAP['udt']}}}"


def _d(value: Any, default: str = "0.00") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _amount(value: Any) -> str:
    return format(_d(value), ".2f")


def _rate(value: Any) -> str:
    return format(_d(value), ".2f")


def _date_102(value: str) -> str:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    raise FacturXBuildError(f"Ungültiges Rechnungsdatum: {value}")


def _add_text(parent: etree._Element, tag: str, text: Any, attrib: dict[str, str] | None = None) -> etree._Element:
    child = etree.SubElement(parent, tag, attrib=attrib or {})
    child.text = "" if text is None else str(text)
    return child


def _add_optional_text(parent: etree._Element, tag: str, text: Any, attrib: dict[str, str] | None = None) -> etree._Element | None:
    if text in (None, ""):
        return None
    return _add_text(parent, tag, text, attrib=attrib)


def _add_party(parent: etree._Element, tag: str, party: dict[str, Any]) -> None:
    party_node = etree.SubElement(parent, tag)
    _add_text(party_node, RAM + "Name", party.get("name"))

    if party.get("vat_id"):
        tax_registration = etree.SubElement(party_node, RAM + "SpecifiedTaxRegistration")
        _add_text(tax_registration, RAM + "ID", party.get("vat_id"), {"schemeID": "VA"})

    if any(party.get(key) for key in ["street", "city", "postal_code", "country"]):
        address = etree.SubElement(party_node, RAM + "PostalTradeAddress")
        _add_optional_text(address, RAM + "PostcodeCode", party.get("postal_code"))
        _add_optional_text(address, RAM + "LineOne", party.get("street"))
        _add_optional_text(address, RAM + "CityName", party.get("city"))
        _add_optional_text(address, RAM + "CountryID", party.get("country"))


def build_facturx_xml_from_invoice(invoice: dict[str, Any]) -> bytes:
    """Build a conservative Factur-X EN16931/CII XML from extracted data.

    This function does not invent missing business data. It expects the app layer
    to have validated required fields first.
    """
    currency = invoice.get("currency") or "EUR"
    seller = invoice.get("seller") or {}
    buyer = invoice.get("buyer") or {}
    totals = invoice.get("totals") or {}
    lines = invoice.get("line_items") or []

    root = etree.Element(RSM + "CrossIndustryInvoice", nsmap=NSMAP)

    context = etree.SubElement(root, RSM + "ExchangedDocumentContext")
    guideline = etree.SubElement(context, RAM + "GuidelineSpecifiedDocumentContextParameter")
    _add_text(guideline, RAM + "ID", "urn:cen.eu:en16931:2017")

    doc = etree.SubElement(root, RSM + "ExchangedDocument")
    _add_text(doc, RAM + "ID", invoice.get("invoice_number"))
    _add_text(doc, RAM + "TypeCode", "380")
    issue_dt = etree.SubElement(doc, RAM + "IssueDateTime")
    _add_text(issue_dt, UDT + "DateTimeString", _date_102(invoice["issue_date"]), {"format": "102"})

    transaction = etree.SubElement(root, RSM + "SupplyChainTradeTransaction")

    tax_bases: dict[Decimal, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for idx, item in enumerate(lines, start=1):
        line = etree.SubElement(transaction, RAM + "IncludedSupplyChainTradeLineItem")

        line_doc = etree.SubElement(line, RAM + "AssociatedDocumentLineDocument")
        _add_text(line_doc, RAM + "LineID", idx)

        product = etree.SubElement(line, RAM + "SpecifiedTradeProduct")
        _add_text(product, RAM + "Name", item.get("description"))

        agreement = etree.SubElement(line, RAM + "SpecifiedLineTradeAgreement")
        price = etree.SubElement(agreement, RAM + "NetPriceProductTradePrice")
        _add_text(price, RAM + "ChargeAmount", _amount(item.get("unit_price")))

        delivery = etree.SubElement(line, RAM + "SpecifiedLineTradeDelivery")
        _add_text(
            delivery,
            RAM + "BilledQuantity",
            item.get("quantity"),
            {"unitCode": item.get("unit_code") or "C62"},
        )

        settlement = etree.SubElement(line, RAM + "SpecifiedLineTradeSettlement")
        tax = etree.SubElement(settlement, RAM + "ApplicableTradeTax")
        _add_text(tax, RAM + "TypeCode", "VAT")
        _add_text(tax, RAM + "CategoryCode", "S")
        _add_text(tax, RAM + "RateApplicablePercent", _rate(item.get("vat_rate")))

        summation = etree.SubElement(settlement, RAM + "SpecifiedTradeSettlementLineMonetarySummation")
        _add_text(summation, RAM + "LineTotalAmount", _amount(item.get("net_amount")))

        tax_bases[_d(item.get("vat_rate"))] += _d(item.get("net_amount"))

    agreement = etree.SubElement(transaction, RAM + "ApplicableHeaderTradeAgreement")
    _add_party(agreement, RAM + "SellerTradeParty", seller)
    _add_party(agreement, RAM + "BuyerTradeParty", buyer)

    delivery = etree.SubElement(transaction, RAM + "ApplicableHeaderTradeDelivery")

    settlement = etree.SubElement(transaction, RAM + "ApplicableHeaderTradeSettlement")
    _add_text(settlement, RAM + "InvoiceCurrencyCode", currency)

    for rate, basis in sorted(tax_bases.items(), key=lambda item: item[0]):
        tax_amount = (basis * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax = etree.SubElement(settlement, RAM + "ApplicableTradeTax")
        _add_text(tax, RAM + "CalculatedAmount", _amount(tax_amount))
        _add_text(tax, RAM + "TypeCode", "VAT")
        _add_text(tax, RAM + "BasisAmount", _amount(basis))
        _add_text(tax, RAM + "CategoryCode", "S")
        _add_text(tax, RAM + "RateApplicablePercent", _rate(rate))

    if invoice.get("payment_reference"):
        payment_ref = etree.SubElement(settlement, RAM + "PaymentReference")
        payment_ref.text = str(invoice["payment_reference"])

    payment_terms = etree.SubElement(settlement, RAM + "SpecifiedTradePaymentTerms")
    _add_text(payment_terms, RAM + "Description", "Zahlbar gemäß Rechnung.")

    header_sum = etree.SubElement(settlement, RAM + "SpecifiedTradeSettlementHeaderMonetarySummation")
    _add_text(header_sum, RAM + "LineTotalAmount", _amount(totals.get("net_total")))
    _add_text(header_sum, RAM + "TaxBasisTotalAmount", _amount(totals.get("net_total")))
    _add_text(header_sum, RAM + "TaxTotalAmount", _amount(totals.get("tax_total")), {"currencyID": currency})
    _add_text(header_sum, RAM + "GrandTotalAmount", _amount(totals.get("gross_total")))
    _add_text(header_sum, RAM + "DuePayableAmount", _amount(totals.get("due_amount") or totals.get("gross_total")))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def validate_facturx_xml(
    facturx_xml_bytes: bytes,
    check_xsd: bool = True,
    check_schematron: bool = False,
    level: str = "en16931",
) -> dict[str, Any]:
    try:
        etree.fromstring(facturx_xml_bytes)
    except etree.XMLSyntaxError as exc:
        return {"ok": False, "error": f"Factur-X-XML ist nicht wohlgeformt: {exc}", "warnings": []}

    warnings: list[str] = []

    try:
        from facturx import xml_check_xsd, xml_check_schematron
    except Exception as exc:
        return {
            "ok": False,
            "error": f"factur-x Library konnte nicht importiert werden: {exc}",
            "warnings": warnings,
        }

    # The Python factur-x package primarily supports standard Factur-X/ZUGFeRD
    # levels such as minimum/basicwl/basic/en16931/extended. Some libraries do
    # not support the separate ZUGFeRD XRECHNUNG profile. For CII XRechnung files
    # the app keeps the original XML and validates with EN16931 where possible.
    supported_level = level.lower()
    if supported_level == "xrechnung":
        warnings.append(
            "Profil XRECHNUNG erkannt. Die lokale factur-x Library unterstützt je nach Version "
            "keine dedizierte XRECHNUNG-Levelprüfung; es wird EN16931 als technische Näherung verwendet."
        )
        supported_level = "en16931"

    if check_xsd:
        try:
            xml_check_xsd(facturx_xml_bytes, flavor="factur-x", level=supported_level)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"XSD-Validierung fehlgeschlagen: {exc}",
                "warnings": warnings,
            }

    if check_schematron:
        try:
            xml_check_schematron(facturx_xml_bytes, flavor="factur-x", level=supported_level)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Schematron-Validierung fehlgeschlagen: {exc}",
                "warnings": warnings,
            }
    else:
        warnings.append("Schematron-Prüfung wurde nicht ausgeführt.")

    return {"ok": True, "error": None, "warnings": warnings}

def embed_xml_in_pdf(
    pdf_bytes: bytes,
    facturx_xml_bytes: bytes,
    check_xsd: bool = False,
    check_schematron: bool = False,
    level: str = "en16931",
) -> bytes:
    try:
        from facturx import generate_from_binary
    except Exception as exc:
        raise FacturXBuildError(f"factur-x Library konnte nicht importiert werden: {exc}") from exc

    supported_level = level.lower()
    if supported_level == "xrechnung":
        # Keep XML content as-is but use EN16931 metadata level if the library
        # cannot generate XRECHNUNG-specific metadata. This is explicitly
        # disclosed in the report by the caller.
        supported_level = "en16931"

    try:
        metadata = {
            "author": "Invoice Converter",
            "keywords": "Factur-X, ZUGFeRD, XRechnung, Invoice",
            "title": "ZUGFeRD invoice",
            "subject": "Factur-X/XRechnung-XML",
        }
        result = generate_from_binary(
            pdf_file=pdf_bytes,
            xml=facturx_xml_bytes,
            flavor="factur-x",
            level=supported_level,
            check_xsd=check_xsd,
            check_schematron=check_schematron,
            pdf_metadata=metadata,
            lang="de-DE",
            afrelationship="Alternative",
        )
    except Exception as exc:
        raise FacturXBuildError(f"XML konnte nicht in die PDF eingebettet werden: {exc}") from exc

    if not result or not result.startswith(b"%PDF"):
        raise FacturXBuildError("Die factur-x Library hat keine gültige PDF zurückgegeben.")

    return result
