from __future__ import annotations

import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree

MAX_XML_BYTES = 10 * 1024 * 1024

REQUIRED_FIELDS = [
    "invoice_number",
    "issue_date",
    "currency",
    "seller.name",
    "buyer.name",
    "line_items",
    "line_items[].description",
    "line_items[].quantity",
    "line_items[].unit_price",
    "line_items[].net_amount",
    "line_items[].vat_rate",
    "totals.net_total",
    "totals.tax_total",
    "totals.gross_total",
]


class XMLValidationError(Exception):
    """Raised when uploaded XML is unsafe or not well-formed."""


def validate_xml_upload(file_name: str, data: bytes) -> etree._Element:
    if not file_name.lower().endswith(".xml"):
        raise XMLValidationError("Die hochgeladene Datei ist keine `.xml`-Datei.")

    if not data:
        raise XMLValidationError("Die XML-Datei ist leer.")

    if len(data) > MAX_XML_BYTES:
        raise XMLValidationError(
            f"Die XML-Datei ist zu groß. Maximal erlaubt sind {MAX_XML_BYTES // (1024 * 1024)} MB."
        )

    if b"<!DOCTYPE" in data[:4096].upper():
        raise XMLValidationError("DOCTYPE/DTD ist aus Sicherheitsgründen nicht erlaubt.")

    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        remove_blank_text=True,
        huge_tree=False,
    )

    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise XMLValidationError(f"XML ist nicht wohlgeformt: {exc}") from exc


def pretty_print_xml(root: etree._Element) -> str:
    return etree.tostring(root, pretty_print=True, encoding="unicode")


def local_name(tag: str) -> str:
    return etree.QName(tag).localname if tag else ""


def normalized_name(tag_or_text: str) -> str:
    name = local_name(tag_or_text)
    return re.sub(r"[^a-z0-9]", "", name.lower())


def text_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def decimal_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        normalized = str(value).strip().replace("€", "").replace("EUR", "").replace(" ", "")
        # German and English number fallback.
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", ".")
        if normalized == "":
            return None
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def first_text(root: etree._Element, paths: list[str]) -> str | None:
    for path in paths:
        result = root.xpath(path)
        if not result:
            continue
        item = result[0]
        if isinstance(item, etree._Element):
            candidate = text_or_none("".join(item.itertext()))
        else:
            candidate = text_or_none(str(item))
        if candidate:
            return candidate
    return None


def first_attr(root: etree._Element, paths: list[str]) -> str | None:
    for path in paths:
        result = root.xpath(path)
        if result:
            candidate = text_or_none(str(result[0]))
            if candidate:
                return candidate
    return None


def _date_yyyymmdd_to_iso(value: str | None) -> str | None:
    value = text_or_none(value)
    if not value:
        return None

    # CII format can be YYYYMMDD.
    if re.fullmatch(r"\d{8}", value):
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"

    # Common German date.
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"

    return value


def _all_text_elements(root: etree._Element) -> list[tuple[str, str, etree._Element]]:
    items: list[tuple[str, str, etree._Element]] = []
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        text = text_or_none(element.text)
        if text:
            items.append((normalized_name(element.tag), text, element))
    return items


def _find_by_name_candidates(
    root: etree._Element,
    candidates: list[str],
    exclude_candidates: list[str] | None = None,
) -> str | None:
    candidate_set = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in candidates}
    exclude_set = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in (exclude_candidates or [])}

    for norm_name, text, _element in _all_text_elements(root):
        if norm_name in exclude_set:
            continue
        if norm_name in candidate_set:
            return text

    # More permissive contains matching for ERP exports.
    for norm_name, text, _element in _all_text_elements(root):
        if any(ex in norm_name for ex in exclude_set):
            continue
        if any(c in norm_name for c in candidate_set):
            return text

    return None


def _find_amount_by_name(root: etree._Element, candidates: list[str]) -> float | None:
    value = _find_by_name_candidates(root, candidates)
    return decimal_or_none(value)


def _first_node(root: etree._Element, paths: list[str]) -> etree._Element | None:
    for path in paths:
        result = root.xpath(path)
        if result and isinstance(result[0], etree._Element):
            return result[0]
    return None


def _find_context_node(root: etree._Element, candidates: list[str]) -> etree._Element | None:
    candidate_set = {re.sub(r"[^a-z0-9]", "", c.lower()) for c in candidates}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        norm = normalized_name(element.tag)
        if norm in candidate_set or any(c in norm for c in candidate_set):
            return element
    return None


def _extract_party_from_node(node: etree._Element | None) -> dict[str, Any]:
    if node is None:
        return {
            "name": None,
            "street": None,
            "city": None,
            "postal_code": None,
            "country": None,
            "vat_id": None,
        }

    def rel(paths: list[str]) -> str | None:
        return first_text(node, paths)

    party = {
        "name": rel([
            ".//*[local-name()='Name']/text()",
            ".//*[local-name()='RegistrationName']/text()",
            ".//*[local-name()='CompanyName']/text()",
            ".//*[local-name()='OrganisationName']/text()",
            ".//*[local-name()='PartyName']/*[local-name()='Name']/text()",
        ]),
        "street": rel([
            ".//*[local-name()='LineOne']/text()",
            ".//*[local-name()='StreetName']/text()",
            ".//*[local-name()='AddressLine']/*[local-name()='Line']/text()",
            ".//*[local-name()='Street']/text()",
            ".//*[local-name()='Strasse']/text()",
        ]),
        "city": rel([
            ".//*[local-name()='CityName']/text()",
            ".//*[local-name()='City']/text()",
            ".//*[local-name()='Ort']/text()",
        ]),
        "postal_code": rel([
            ".//*[local-name()='PostcodeCode']/text()",
            ".//*[local-name()='PostalZone']/text()",
            ".//*[local-name()='Zip']/text()",
            ".//*[local-name()='PLZ']/text()",
        ]),
        "country": rel([
            ".//*[local-name()='CountryID']/text()",
            ".//*[local-name()='Country']/*[local-name()='IdentificationCode']/text()",
            ".//*[local-name()='CountryCode']/text()",
            ".//*[local-name()='Land']/text()",
        ]),
        "vat_id": rel([
            ".//*[local-name()='SpecifiedTaxRegistration']/*[local-name()='ID']/text()",
            ".//*[local-name()='PartyTaxScheme']/*[local-name()='CompanyID']/text()",
            ".//*[local-name()='VATID']/text()",
            ".//*[local-name()='UStID']/text()",
            ".//*[local-name()='UmsatzsteuerID']/text()",
        ]),
    }

    # Generic fallback inside the party node.
    if not party["name"]:
        party["name"] = _find_by_name_candidates(node, ["name", "company", "companyname", "firma", "organisation"])
    if not party["street"]:
        party["street"] = _find_by_name_candidates(node, ["street", "streetname", "strasse", "addressline", "lineone"])
    if not party["city"]:
        party["city"] = _find_by_name_candidates(node, ["city", "cityname", "ort"])
    if not party["postal_code"]:
        party["postal_code"] = _find_by_name_candidates(node, ["postcode", "postcodecode", "postalzone", "zip", "plz"])
    if not party["country"]:
        party["country"] = _find_by_name_candidates(node, ["country", "countryid", "countrycode", "land"])
    if not party["vat_id"]:
        party["vat_id"] = _find_by_name_candidates(node, ["vatid", "ustid", "umsatzsteuerid", "taxid"])

    return party


def _extract_lines(root: etree._Element) -> list[dict[str, Any]]:
    line_nodes = root.xpath(
        "//*[local-name()='IncludedSupplyChainTradeLineItem'] | "
        "//*[local-name()='InvoiceLine'] | "
        "//*[local-name()='LineItem'] | "
        "//*[local-name()='Position'] | "
        "//*[local-name()='InvoicePosition'] | "
        "//*[local-name()='Rechnungsposition']"
    )

    lines: list[dict[str, Any]] = []
    for line in line_nodes:
        description = first_text(line, [
            ".//*[local-name()='SpecifiedTradeProduct']/*[local-name()='Name']/text()",
            ".//*[local-name()='Item']/*[local-name()='Description']/text()",
            ".//*[local-name()='Item']/*[local-name()='Name']/text()",
            ".//*[local-name()='Description']/text()",
            ".//*[local-name()='Name']/text()",
            ".//*[local-name()='Bezeichnung']/text()",
            ".//*[local-name()='Beschreibung']/text()",
        ]) or _find_by_name_candidates(line, ["description", "name", "bezeichnung", "beschreibung", "artikel"])

        quantity = decimal_or_none(first_text(line, [
            ".//*[local-name()='BilledQuantity']/text()",
            ".//*[local-name()='InvoicedQuantity']/text()",
            ".//*[local-name()='Quantity']/text()",
            ".//*[local-name()='Menge']/text()",
        ])) or _find_amount_by_name(line, ["quantity", "billedquantity", "invoicedquantity", "menge", "anzahl"])

        unit_code = first_attr(line, [
            ".//*[local-name()='BilledQuantity']/@unitCode",
            ".//*[local-name()='InvoicedQuantity']/@unitCode",
            ".//*[local-name()='Quantity']/@unitCode",
        ]) or _find_by_name_candidates(line, ["unitcode", "unit", "einheit"]) or "C62"

        unit_price = decimal_or_none(first_text(line, [
            ".//*[local-name()='NetPriceProductTradePrice']/*[local-name()='ChargeAmount']/text()",
            ".//*[local-name()='Price']/*[local-name()='PriceAmount']/text()",
            ".//*[local-name()='UnitPrice']/text()",
            ".//*[local-name()='Einzelpreis']/text()",
        ])) or _find_amount_by_name(line, ["unitprice", "priceamount", "einzelpreis", "nettopreis"])

        net_amount = decimal_or_none(first_text(line, [
            ".//*[local-name()='SpecifiedTradeSettlementLineMonetarySummation']/*[local-name()='LineTotalAmount']/text()",
            ".//*[local-name()='LineExtensionAmount']/text()",
            ".//*[local-name()='LineTotalAmount']/text()",
            ".//*[local-name()='NetAmount']/text()",
            ".//*[local-name()='Nettobetrag']/text()",
        ])) or _find_amount_by_name(line, ["linetotalamount", "lineextensionamount", "netamount", "nettobetrag", "positionsbetrag"])

        vat_category = first_text(line, [
            ".//*[local-name()='ApplicableTradeTax']/*[local-name()='CategoryCode']/text()",
            ".//*[local-name()='ClassifiedTaxCategory']/*[local-name()='ID']/text()",
            ".//*[local-name()='TaxCategory']/text()",
            ".//*[local-name()='Steuerkategorie']/text()",
        ]) or _find_by_name_candidates(line, ["categorycode", "taxcategory", "steuerkategorie"])

        vat_rate = decimal_or_none(first_text(line, [
            ".//*[local-name()='ApplicableTradeTax']/*[local-name()='RateApplicablePercent']/text()",
            ".//*[local-name()='ClassifiedTaxCategory']/*[local-name()='Percent']/text()",
            ".//*[local-name()='TaxRate']/text()",
            ".//*[local-name()='VATRate']/text()",
            ".//*[local-name()='Steuersatz']/text()",
        ])) or _find_amount_by_name(line, ["rateapplicablepercent", "taxrate", "vatrate", "steuersatz", "mwst"])

        if any(value is not None for value in [description, quantity, unit_price, net_amount, vat_rate]):
            # If unit price or net amount is missing but quantity enables calculation, derive only arithmetically.
            if unit_price is None and quantity not in (None, 0) and net_amount is not None:
                unit_price = round(float(net_amount) / float(quantity), 4)
            if net_amount is None and unit_price is not None and quantity is not None:
                net_amount = round(float(unit_price) * float(quantity), 2)

            lines.append({
                "description": description,
                "quantity": quantity,
                "unit_code": unit_code,
                "unit_price": unit_price,
                "net_amount": net_amount,
                "vat_category": vat_category,
                "vat_rate": vat_rate,
            })

    return lines


def extract_invoice_data(root: etree._Element) -> dict[str, Any]:
    seller_node = _first_node(root, [
        "//*[local-name()='SellerTradeParty']",
        "//*[local-name()='AccountingSupplierParty']/*[local-name()='Party']",
        "//*[local-name()='Seller']",
        "//*[local-name()='Supplier']",
        "//*[local-name()='Verkaeufer']",
        "//*[local-name()='Verkäufer']",
        "//*[local-name()='Lieferant']",
    ]) or _find_context_node(root, ["seller", "supplier", "verkaeufer", "verkäufer", "lieferant"])

    buyer_node = _first_node(root, [
        "//*[local-name()='BuyerTradeParty']",
        "//*[local-name()='AccountingCustomerParty']/*[local-name()='Party']",
        "//*[local-name()='Buyer']",
        "//*[local-name()='Customer']",
        "//*[local-name()='Kaeufer']",
        "//*[local-name()='Käufer']",
        "//*[local-name()='Kunde']",
    ]) or _find_context_node(root, ["buyer", "customer", "kaeufer", "käufer", "kunde"])

    invoice_number = first_text(root, [
        "/*[local-name()='CrossIndustryInvoice']/*[local-name()='ExchangedDocument']/*[local-name()='ID']/text()",
        "/*[local-name()='Invoice']/*[local-name()='ID']/text()",
        "//*[local-name()='InvoiceNumber']/text()",
        "//*[local-name()='InvoiceNo']/text()",
        "//*[local-name()='Rechnungsnummer']/text()",
        "//*[local-name()='Belegnummer']/text()",
    ]) or _find_by_name_candidates(root, [
        "invoicenumber", "invoiceno", "invoiceid", "rechnungnummer", "rechnungsnummer", "belegnummer", "documentnumber"
    ], exclude_candidates=["buyerreference"])

    issue_date = first_text(root, [
        "/*[local-name()='CrossIndustryInvoice']/*[local-name()='ExchangedDocument']/*[local-name()='IssueDateTime']//*[local-name()='DateTimeString']/text()",
        "/*[local-name()='Invoice']/*[local-name()='IssueDate']/text()",
        "//*[local-name()='InvoiceDate']/text()",
        "//*[local-name()='IssueDate']/text()",
        "//*[local-name()='Rechnungsdatum']/text()",
        "//*[local-name()='Belegdatum']/text()",
    ]) or _find_by_name_candidates(root, ["invoicedate", "issuedate", "rechnungsdatum", "belegdatum", "datum"])

    due_date = first_text(root, [
        "//*[local-name()='SpecifiedTradePaymentTerms']/*[local-name()='DueDateDateTime']//*[local-name()='DateTimeString']/text()",
        "//*[local-name()='DueDate']/text()",
        "//*[local-name()='PaymentDueDate']/text()",
        "//*[local-name()='Faelligkeitsdatum']/text()",
        "//*[local-name()='Fälligkeitsdatum']/text()",
    ]) or _find_by_name_candidates(root, ["duedate", "paymentduedate", "faelligkeitsdatum", "fälligkeitsdatum"])

    currency = first_text(root, [
        "//*[local-name()='ApplicableHeaderTradeSettlement']/*[local-name()='InvoiceCurrencyCode']/text()",
        "/*[local-name()='Invoice']/*[local-name()='DocumentCurrencyCode']/text()",
        "//*[local-name()='Currency']/text()",
        "//*[local-name()='CurrencyCode']/text()",
        "//*[local-name()='Waehrung']/text()",
        "//*[local-name()='Währung']/text()",
    ]) or _find_by_name_candidates(root, ["currency", "currencycode", "waehrung", "währung"]) or "EUR"

    guideline_id = first_text(root, [
        "//*[local-name()='GuidelineSpecifiedDocumentContextParameter']/*[local-name()='ID']/text()",
        "//*[local-name()='CustomizationID']/text()",
    ]) or _find_by_name_candidates(root, ["guideline", "guidelineid", "customizationid", "profileid"])

    buyer_reference = first_text(root, [
        "//*[local-name()='BuyerReference']/text()",
        "//*[local-name()='BuyerOrderReferencedDocument']/*[local-name()='IssuerAssignedID']/text()",
        "//*[local-name()='OrderReference']/*[local-name()='ID']/text()",
        "//*[local-name()='LeitwegID']/text()",
    ]) or _find_by_name_candidates(root, ["buyerreference", "leitwegid", "bestellnummer", "orderreference"])

    line_items = _extract_lines(root)

    tax_nodes = root.xpath(
        "//*[local-name()='ApplicableHeaderTradeSettlement']/*[local-name()='ApplicableTradeTax'] | "
        "//*[local-name()='TaxTotal']/*[local-name()='TaxSubtotal'] | "
        "//*[local-name()='TaxBreakdown'] | "
        "//*[local-name()='Steueraufschluesselung'] | "
        "//*[local-name()='Steueraufschlüsselung']"
    )
    tax_breakdown: list[dict[str, Any]] = []
    for tax in tax_nodes:
        item = {
            "category_code": first_text(tax, [
                ".//*[local-name()='CategoryCode']/text()",
                ".//*[local-name()='TaxCategory']/*[local-name()='ID']/text()",
                ".//*[local-name()='Steuerkategorie']/text()",
            ]) or _find_by_name_candidates(tax, ["categorycode", "taxcategory", "steuerkategorie"]),
            "rate": decimal_or_none(first_text(tax, [
                ".//*[local-name()='RateApplicablePercent']/text()",
                ".//*[local-name()='Percent']/text()",
                ".//*[local-name()='Steuersatz']/text()",
            ])) or _find_amount_by_name(tax, ["rateapplicablepercent", "percent", "steuersatz", "taxrate"]),
            "basis_amount": decimal_or_none(first_text(tax, [
                ".//*[local-name()='BasisAmount']/text()",
                ".//*[local-name()='TaxableAmount']/text()",
                ".//*[local-name()='Bemessungsgrundlage']/text()",
            ])) or _find_amount_by_name(tax, ["basisamount", "taxableamount", "bemessungsgrundlage"]),
            "tax_amount": decimal_or_none(first_text(tax, [
                ".//*[local-name()='CalculatedAmount']/text()",
                ".//*[local-name()='TaxAmount']/text()",
                ".//*[local-name()='Steuerbetrag']/text()",
            ])) or _find_amount_by_name(tax, ["calculatedamount", "taxamount", "steuerbetrag"]),
        }
        if any(value not in (None, "") for value in item.values()):
            tax_breakdown.append(item)

    totals = {
        "net_total": decimal_or_none(first_text(root, [
            "//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']/*[local-name()='LineTotalAmount']/text()",
            "//*[local-name()='LegalMonetaryTotal']/*[local-name()='TaxExclusiveAmount']/text()",
            "//*[local-name()='NetTotal']/text()",
            "//*[local-name()='NetAmount']/text()",
            "//*[local-name()='Nettobetrag']/text()",
            "//*[local-name()='Nettosumme']/text()",
        ])) or _find_amount_by_name(root, ["nettotal", "netamount", "taxexclusiveamount", "nettobetrag", "nettosumme"]),
        "tax_total": decimal_or_none(first_text(root, [
            "//*[local-name()='ApplicableHeaderTradeSettlement']/*[local-name()='TaxTotalAmount']/text()",
            "//*[local-name()='TaxTotal']/*[local-name()='TaxAmount']/text()",
            "//*[local-name()='TaxTotalAmount']/text()",
            "//*[local-name()='Steuerbetrag']/text()",
            "//*[local-name()='Steuersumme']/text()",
        ])) or _find_amount_by_name(root, ["taxtotal", "taxtotalamount", "taxamount", "steuerbetrag", "steuersumme", "mwstbetrag"]),
        "gross_total": decimal_or_none(first_text(root, [
            "//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']/*[local-name()='GrandTotalAmount']/text()",
            "//*[local-name()='LegalMonetaryTotal']/*[local-name()='TaxInclusiveAmount']/text()",
            "//*[local-name()='GrossTotal']/text()",
            "//*[local-name()='Bruttobetrag']/text()",
            "//*[local-name()='Bruttosumme']/text()",
        ])) or _find_amount_by_name(root, ["grosstotal", "grandtotalamount", "taxinclusiveamount", "bruttobetrag", "bruttosumme", "gesamtbetrag"]),
        "due_amount": decimal_or_none(first_text(root, [
            "//*[local-name()='SpecifiedTradeSettlementHeaderMonetarySummation']/*[local-name()='DuePayableAmount']/text()",
            "//*[local-name()='LegalMonetaryTotal']/*[local-name()='PayableAmount']/text()",
            "//*[local-name()='DueAmount']/text()",
            "//*[local-name()='Zahlbetrag']/text()",
        ])) or _find_amount_by_name(root, ["duepayableamount", "payableamount", "dueamount", "zahlbetrag"]),
    }

    # Safe arithmetic fallback for totals only when source values are present.
    if totals["gross_total"] is None and totals["net_total"] is not None and totals["tax_total"] is not None:
        totals["gross_total"] = round(float(totals["net_total"]) + float(totals["tax_total"]), 2)
    if totals["tax_total"] is None and totals["net_total"] is not None and totals["gross_total"] is not None:
        totals["tax_total"] = round(float(totals["gross_total"]) - float(totals["net_total"]), 2)
    if totals["due_amount"] is None:
        totals["due_amount"] = totals["gross_total"]

    bank_details = {
        "iban": first_text(root, [
            "//*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='IBANID']/text()",
            "//*[local-name()='PayeeFinancialAccount']/*[local-name()='ID']/text()",
            "//*[local-name()='IBAN']/text()",
        ]) or _find_by_name_candidates(root, ["iban", "ibanid"]),
        "bic": first_text(root, [
            "//*[local-name()='PayeeSpecifiedCreditorFinancialInstitution']/*[local-name()='BICID']/text()",
            "//*[local-name()='FinancialInstitutionBranch']/*[local-name()='ID']/text()",
            "//*[local-name()='BIC']/text()",
        ]) or _find_by_name_candidates(root, ["bic", "bicid", "swift"]),
        "account_name": first_text(root, [
            "//*[local-name()='PayeePartyCreditorFinancialAccount']/*[local-name()='AccountName']/text()",
            "//*[local-name()='PayeeFinancialAccount']/*[local-name()='Name']/text()",
            "//*[local-name()='Kontoinhaber']/text()",
        ]) or _find_by_name_candidates(root, ["accountname", "kontoinhaber"]),
        "payment_means_type_code": first_text(root, [
            "//*[local-name()='SpecifiedTradeSettlementPaymentMeans']/*[local-name()='TypeCode']/text()",
            "//*[local-name()='PaymentMeans']/*[local-name()='PaymentMeansCode']/text()",
        ]) or _find_by_name_candidates(root, ["paymentmeanstypecode", "paymentmeanscode", "zahlart"]),
    }

    return {
        "invoice_number": invoice_number,
        "issue_date": _date_yyyymmdd_to_iso(issue_date),
        "due_date": _date_yyyymmdd_to_iso(due_date),
        "currency": currency,
        "guideline_id": guideline_id,
        "buyer_reference": buyer_reference,
        "seller": _extract_party_from_node(seller_node),
        "buyer": _extract_party_from_node(buyer_node),
        "bank_details": bank_details,
        "line_items": line_items,
        "tax_breakdown": tax_breakdown,
        "totals": totals,
        "payment_reference": first_text(root, [
            "//*[local-name()='PaymentReference']/text()",
            "//*[local-name()='PaymentID']/text()",
            "//*[local-name()='Zahlungsreferenz']/text()",
        ]) or _find_by_name_candidates(root, ["paymentreference", "paymentid", "zahlungsreferenz", "verwendungszweck"]),
        "payment_terms": first_text(root, [
            "//*[local-name()='SpecifiedTradePaymentTerms']/*[local-name()='Description']/text()",
            "//*[local-name()='PaymentTerms']/*[local-name()='Note']/text()",
            "//*[local-name()='PaymentTerms']/text()",
            "//*[local-name()='Zahlungsbedingungen']/text()",
        ]) or _find_by_name_candidates(root, ["paymentterms", "zahlungsbedingungen", "zahlungsziel"]),
        "notes": [
            note for note in [
                first_text(root, ["/*[local-name()='CrossIndustryInvoice']/*[local-name()='ExchangedDocument']/*[local-name()='IncludedNote']/*[local-name()='Content']/text()"]),
                first_text(root, ["/*[local-name()='Invoice']/*[local-name()='Note']/text()"]),
                _find_by_name_candidates(root, ["note", "bemerkung", "hinweis"]),
            ] if note
        ],
    }


def get_xml_structure_summary(root: etree._Element) -> dict[str, Any]:
    element_counts: dict[str, int] = {}
    namespaces: dict[str, str] = {}
    for prefix, uri in (root.nsmap or {}).items():
        namespaces[prefix or "default"] = uri

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        name = local_name(element.tag)
        element_counts[name] = element_counts.get(name, 0) + 1

    guideline = first_text(root, [
        "//*[local-name()='GuidelineSpecifiedDocumentContextParameter']/*[local-name()='ID']/text()",
        "//*[local-name()='CustomizationID']/text()",
    ])

    top_elements = sorted(element_counts.items(), key=lambda item: item[1], reverse=True)[:40]
    return {
        "root": local_name(root.tag),
        "root_namespace": etree.QName(root).namespace,
        "guideline_id": guideline,
        "namespaces": namespaces,
        "top_element_counts": dict(top_elements),
        "supported_mode": (
            "Namespace-unabhängige Extraktion für Factur-X/CII, XRechnung-CII, "
            "UBL-nahe Rechnungen und generische ERP-XMLs. Unbekannte XMLs werden "
            "analysiert, aber fehlende Pflichtdaten werden nicht erfunden."
        ),
    }


def is_likely_facturx_xml(root: etree._Element) -> bool:
    tag = root.tag or ""
    guideline = first_text(root, [
        "//*[local-name()='GuidelineSpecifiedDocumentContextParameter']/*[local-name()='ID']/text()",
        "//*[local-name()='CustomizationID']/text()",
    ]) or ""

    if tag.endswith("CrossIndustryInvoice"):
        return True

    guideline_lower = guideline.lower()
    return any(token in guideline_lower for token in ["factur-x", "zugferd", "xrechnung", "en16931"])


def _get_nested(data: dict[str, Any], dotted: str) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def validate_required_invoice_fields(invoice: dict[str, Any]) -> list[str]:
    missing: list[str] = []

    for field in ["invoice_number", "issue_date", "currency", "seller.name", "buyer.name"]:
        if not _get_nested(invoice, field):
            missing.append(field)

    lines = invoice.get("line_items") or []
    if not lines:
        missing.append("line_items")
    else:
        for idx, line in enumerate(lines, start=1):
            for field in ["description", "quantity", "unit_price", "net_amount", "vat_rate"]:
                if line.get(field) in (None, ""):
                    missing.append(f"line_items[{idx}].{field}")

    totals = invoice.get("totals") or {}
    for field in ["net_total", "tax_total", "gross_total"]:
        if totals.get(field) in (None, ""):
            missing.append(f"totals.{field}")

    return missing


def coalesce_invoice_data(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """Merge two invoice dictionaries without inventing values."""
    result = deepcopy(primary) if primary else {}
    fallback = fallback or {}

    def merge_dict(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        for key, value in source.items():
            if key not in target or target[key] in (None, "", [], {}):
                target[key] = deepcopy(value)
            elif isinstance(target[key], dict) and isinstance(value, dict):
                target[key] = merge_dict(target[key], value)
            elif isinstance(target[key], list) and not target[key] and value:
                target[key] = deepcopy(value)
        return target

    return merge_dict(result, fallback)


def build_conversion_report(
    status: str,
    context: dict[str, Any],
    error_message: str | None = None,
) -> str:
    lines = [
        "LENCO .xml -> ZUGFeRD converter",
        "Konvertierungsbericht",
        "=" * 40,
        f"Status: {status}",
        f"Datei: {context.get('file_name', 'unbekannt')}",
        "",
    ]

    if error_message:
        lines.extend(["Fehler:", error_message, ""])

    lines.extend([
        "Erkannte XML-Struktur:",
        json.dumps(context.get("detected_structure", {}), ensure_ascii=False, indent=2),
        "",
        "Abdeckung / Mapping-Hinweis:",
        (
            "Die App verarbeitet XML namespace-unabhängig und unterstützt typische "
            "E-Rechnungsformate wie Factur-X/CII, ZUGFeRD, XRechnung-CII, "
            "UBL-nahe XMLs sowie viele ERP-XMLs über generische Heuristiken. "
            "Eine mathematische Garantie für jedes beliebige XML-Schema ist nicht möglich; "
            "nicht eindeutig erkennbare Pflichtdaten werden im Fehlerbericht ausgewiesen."
        ),
        "",
        "Fehlende Pflichtfelder:",
    ])

    missing = context.get("missing_required_fields") or []
    lines.extend([f"- {field}" for field in missing] or ["- keine"])

    lines.extend(["", "Validierungsfehler:"])
    validation_errors = context.get("validation_errors") or []
    lines.extend([f"- {err}" for err in validation_errors] or ["- keine"])

    lines.extend(["", "Warnungen:"])
    warnings = context.get("warnings") or []
    lines.extend([f"- {warning}" for warning in warnings] or ["- keine"])

    lines.extend([
        "",
        "Hinweis:",
        (
            "Eine finale rechtliche/technische Compliance sollte zusätzlich mit "
            "externen Validatoren wie veraPDF und einem ZUGFeRD-/Factur-X-/XRechnung-Validator "
            "geprüft werden."
        ),
    ])

    return "\n".join(lines)
