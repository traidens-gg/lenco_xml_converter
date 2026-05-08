from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import traceback
from typing import Any

import streamlit as st

from pdf_renderer import render_invoice_pdf
from xml_utils import (
    XMLValidationError,
    build_conversion_report,
    extract_invoice_data,
    get_xml_structure_summary,
    is_likely_facturx_xml,
    pretty_print_xml,
    validate_required_invoice_fields,
    validate_xml_upload,
)
from zugferd_builder import (
    FacturXBuildError,
    build_facturx_xml_from_invoice,
    embed_xml_in_pdf,
    validate_facturx_xml,
)

APP_TITLE = "lenco .xml -> ZUGFeRD converter"
DOWNLOAD_PDF_NAME = "zugferd_invoice.pdf"
DOWNLOAD_REPORT_NAME = "conversion_report.txt"
LOGO_URL = "https://i.ibb.co/H0HpVBd/Logo-Neu.png"
LOGO_PATH = Path(__file__).parent / "assets" / "logo_Lenco.svg"

PRIVACY_DISCLAIMER = """
Datenschutzhinweis: Die Verarbeitung erfolgt ausschließlich zur technischen Konvertierung
der hochgeladenen XML-Rechnung in der laufenden App-Sitzung. Die App speichert keine
hochgeladenen XML-Dateien dauerhaft, legt keine Rechnungsdaten in einer Datenbank ab,
schreibt keine XML-Inhalte in Logs und übermittelt keine Rechnungsdaten an KI-Dienste
oder sonstige Drittanbieter. Für den Download werden die erzeugte PDF-Datei und ein
technischer Bericht nur temporär im Arbeitsspeicher der aktuellen Streamlit-Sitzung
vorgehalten. Über „Sitzungsdaten löschen“ können diese temporären Daten unmittelbar
entfernt werden.
""".strip()


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "file_hash": None,
        "status": "idle",
        "pdf_bytes": None,
        "report_text": None,
        "warnings": [],
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _clear_generated_session_data_after_download() -> None:
    """Clear generated in-memory files after the PDF download is triggered.

    The uploaded file widget may still visually show the selected file in the
    browser. Keeping file_hash prevents an immediate re-processing loop after
    Streamlit reruns, while generated PDF/report bytes are removed from session
    state.
    """
    st.session_state.status = "downloaded"
    st.session_state.pdf_bytes = None
    st.session_state.report_text = None
    st.session_state.warnings = []


def _uploaded_file_hash(file_name: str, data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(file_name.encode("utf-8", errors="ignore"))
    digest.update(data)
    return digest.hexdigest()



def _logo_src() -> str:
    """Return the external LENCO logo URL, with local SVG fallback."""
    if LOGO_URL:
        return LOGO_URL

    try:
        svg_bytes = LOGO_PATH.read_bytes()
    except FileNotFoundError:
        return ""

    encoded = base64.b64encode(svg_bytes).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _detect_requested_level(invoice_data: dict[str, Any]) -> str:
    guideline = str(invoice_data.get("guideline_id") or "").lower()
    if "xrechnung" in guideline:
        return "xrechnung"
    return "en16931"


def _inject_css() -> None:
    st.markdown(
        """
        <style>
            :root {
                --lenco-bordeaux: #8A2B2B;
                --lenco-bordeaux-dark: #6F2020;
                --lenco-anthracite: #555555;
                --lenco-text: #333333;
                --lenco-muted: #777777;
                --lenco-bg: #F7F5F2;
                --lenco-card: #FFFFFF;
                --lenco-line: #E4E0DB;
                --lenco-soft: #F1EDEA;
            }

            .stApp {
                background:
                    radial-gradient(circle at 50% 0%, rgba(138, 43, 43, 0.08), transparent 30%),
                    linear-gradient(180deg, #FBFAF8 0%, var(--lenco-bg) 100%);
                color: var(--lenco-text);
                font-family: Arial, Helvetica, sans-serif;
            }

            .block-container {
                max-width: 1120px;
                padding-top: 0.85rem;
                padding-bottom: 1.25rem;
                margin-left: auto;
                margin-right: auto;
            }

            header[data-testid="stHeader"] {
                background: transparent;
            }

            section[data-testid="stSidebar"] {
                background: linear-gradient(180deg, #FFFFFF 0%, #F4F1EE 100%);
                border-right: 1px solid var(--lenco-line);
            }

            section[data-testid="stSidebar"] * {
                color: var(--lenco-text);
            }

            .sidebar-brand {
                padding: 0.15rem 0 0.85rem 0;
                border-bottom: 1px solid var(--lenco-line);
                margin-bottom: 0.9rem;
                text-align: center;
            }

            .sidebar-brand-logo-img {
                width: 132px;
                max-width: 82%;
                height: auto;
                display: block;
                margin: 0 auto;
            }


            .sidebar-brand-subline {
                margin-top: 0.28rem;
                color: var(--lenco-muted);
                font-size: 0.72rem;
                letter-spacing: 0.13em;
                text-transform: uppercase;
            }

            .sidebar-section-title {
                margin: 0 0 0.45rem 0;
                color: var(--lenco-anthracite);
                font-weight: 700;
                font-size: 0.9rem;
                text-align: center;
            }

            .hero-card {
                background: var(--lenco-card);
                border: 1px solid var(--lenco-line);
                border-radius: 22px;
                padding: 1.05rem 1.65rem 0.95rem 1.65rem;
                box-shadow: 0 14px 34px rgba(85, 85, 85, 0.08);
                margin: 0 auto 0.7rem auto;
                position: relative;
                overflow: hidden;
                text-align: center;
            }

            .hero-card:before {
                content: "";
                position: absolute;
                top: 0;
                left: 0;
                width: 6px;
                height: 100%;
                background: var(--lenco-bordeaux);
            }

            .brand-logo-img {
                width: 128px;
                max-width: 42%;
                height: auto;
                display: block;
                margin: 0 auto 0.65rem auto;
            }

            .hero-title {
                color: var(--lenco-anthracite);
                font-size: clamp(1.7rem, 3vw, 2.35rem);
                font-weight: 800;
                letter-spacing: -0.045em;
                line-height: 1.05;
                margin: 0 auto;
                text-align: center;
            }

            .hero-title .accent {
                color: var(--lenco-bordeaux);
            }

            .hero-subtitle {
                color: var(--lenco-muted);
                font-size: 0.92rem;
                line-height: 1.42;
                max-width: 980px;
                margin: 0.65rem auto 0 auto;
                text-align: center;
            }

            .neutral-note {
                background: #FFFFFF;
                border: 1px solid var(--lenco-line);
                border-left: 5px solid var(--lenco-bordeaux);
                border-radius: 16px;
                padding: 0.68rem 0.9rem;
                color: var(--lenco-muted);
                line-height: 1.38;
                margin: 0 auto 0.7rem auto;
                text-align: center;
                font-size: 0.88rem;
                box-shadow: 0 8px 18px rgba(85, 85, 85, 0.035);
            }

            .upload-head-card {
                background: var(--lenco-card);
                border: 1px solid var(--lenco-line);
                border-radius: 20px;
                padding: 0.75rem 1.35rem 0.68rem 1.35rem;
                box-shadow: 0 10px 24px rgba(85, 85, 85, 0.055);
                margin: 0.65rem auto 0.55rem auto;
                text-align: center;
            }

            .upload-title {
                color: var(--lenco-anthracite);
                font-weight: 800;
                font-size: 1.08rem;
                margin: 0 0 0.25rem 0;
                text-align: center;
            }

            .upload-caption {
                color: var(--lenco-muted);
                font-size: 0.88rem;
                margin: 0 auto;
                max-width: 960px;
                text-align: center;
                line-height: 1.38;
            }

            .upload-arrow {
                width: 38px;
                height: 38px;
                margin: 0.55rem auto 0 auto;
                border-radius: 50%;
                background: rgba(138, 43, 43, 0.10);
                border: 1px solid rgba(138, 43, 43, 0.25);
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--lenco-bordeaux);
                font-size: 1.25rem;
                font-weight: 900;
            }

            div[data-testid="stFileUploader"] {
                max-width: 1120px;
                margin-left: auto;
                margin-right: auto;
                margin-bottom: 0.55rem;
            }

            div[data-testid="stFileUploader"] section {
                border: 2px dashed rgba(138, 43, 43, 0.45) !important;
                background:
                    linear-gradient(180deg, rgba(138, 43, 43, 0.03), rgba(255, 255, 255, 0.95)),
                    #FFFFFF !important;
                border-radius: 22px !important;
                min-height: 104px;
                padding: 0.85rem 1.4rem !important;
                box-shadow: 0 12px 28px rgba(138, 43, 43, 0.07);
                display: flex;
                align-items: center;
                justify-content: center;
                text-align: center;
            }

            div[data-testid="stFileUploader"] section:hover {
                border-color: var(--lenco-bordeaux) !important;
                background:
                    linear-gradient(180deg, rgba(138, 43, 43, 0.055), rgba(255, 255, 255, 0.96)),
                    #FFFFFF !important;
            }

            div[data-testid="stFileUploaderDropzone"] {
                width: 100%;
                justify-content: center;
            }

            div[data-testid="stFileUploaderDropzoneInstructions"] {
                text-align: center;
            }

            div[data-testid="stFileUploaderDropzoneInstructions"] span,
            div[data-testid="stFileUploaderDropzoneInstructions"] small {
                color: var(--lenco-muted) !important;
                font-size: 0.9rem !important;
            }

            div[data-testid="stFileUploader"] button {
                background: var(--lenco-bordeaux) !important;
                color: #FFFFFF !important;
                border: 1px solid var(--lenco-bordeaux) !important;
                border-radius: 13px !important;
                padding: 0.62rem 1.05rem !important;
                font-weight: 800 !important;
                box-shadow: 0 10px 20px rgba(138, 43, 43, 0.16);
            }

            .status-card {
                border-radius: 16px;
                padding: 0.72rem 1rem;
                margin: 0.55rem auto 0.55rem auto;
                border: 1px solid transparent;
                font-weight: 700;
                text-align: center;
                font-size: 0.95rem;
            }

            .status-success {
                background: #EAF7EF;
                border-color: #BFE6CB;
                color: #166534;
            }

            .status-error {
                background: #FFF1F1;
                border-color: #F3BBBB;
                color: #9F1D1D;
            }

            .status-neutral {
                background: #F1F5F9;
                border-color: #D7DEE8;
                color: #334155;
            }

            .privacy-disclaimer {
                max-width: 1120px;
                margin: 0.75rem auto 0 auto;
                background: #FFFFFF;
                border: 1px solid var(--lenco-line);
                border-radius: 14px;
                padding: 0.62rem 0.8rem;
                color: var(--lenco-muted);
                font-size: 0.76rem;
                line-height: 1.35;
                box-shadow: 0 8px 18px rgba(85, 85, 85, 0.035);
                text-align: left;
            }

            .privacy-disclaimer strong {
                color: var(--lenco-anthracite);
            }

            div[data-testid="stDownloadButton"] {
                text-align: center;
                margin-top: 0.25rem;
                margin-bottom: 0.35rem;
            }

            div[data-testid="stDownloadButton"] button,
            div.stButton > button {
                background: var(--lenco-bordeaux);
                color: #FFFFFF;
                border: 1px solid var(--lenco-bordeaux);
                border-radius: 14px;
                padding: 0.65rem 0.95rem;
                font-weight: 750;
                box-shadow: 0 10px 20px rgba(138, 43, 43, 0.16);
                transition: all 0.15s ease-in-out;
            }

            div[data-testid="stDownloadButton"] button:hover,
            div.stButton > button:hover {
                background: var(--lenco-bordeaux-dark);
                border-color: var(--lenco-bordeaux-dark);
                color: #FFFFFF;
                transform: translateY(-1px);
            }

            div[data-testid="stExpander"] {
                border: 1px solid var(--lenco-line);
                border-radius: 14px;
                background: #FFFFFF;
                box-shadow: 0 8px 18px rgba(85, 85, 85, 0.04);
                overflow: hidden;
                max-width: 1120px;
                margin: 0.45rem auto;
            }

            div[data-testid="stAlert"] {
                border-radius: 14px;
                border: 1px solid rgba(138, 43, 43, 0.18);
                max-width: 1120px;
                margin-left: auto;
                margin-right: auto;
                padding-top: 0.55rem;
                padding-bottom: 0.55rem;
            }

            .stCheckbox label {
                font-weight: 600;
            }


            @media (min-width: 1200px) {
                .block-container {
                    max-width: 1180px;
                }

                div[data-testid="stFileUploader"],
                div[data-testid="stExpander"],
                div[data-testid="stAlert"],
                .privacy-disclaimer {
                    max-width: 1120px;
                }
            }

            @media (max-width: 760px) {
                .block-container {
                    padding-top: 0.7rem;
                }

                .hero-card {
                    padding: 1rem 1rem 0.9rem 1rem;
                    border-radius: 20px;
                }

                .brand-logo-img {
                width: 128px;
                max-width: 42%;
                height: auto;
                display: block;
                margin: 0 auto 0.65rem auto;
            }

                div[data-testid="stFileUploader"] section {
                    min-height: 108px;
                    padding: 0.9rem 0.8rem !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> tuple[bool, bool]:
    logo_uri = _logo_src()
    logo_html = (
        f'<img class="sidebar-brand-logo-img" src="{logo_uri}" alt="LENCO Logo" />'
        if logo_uri
        else '<strong>lenco</strong>'
    )

    with st.sidebar:
        st.markdown(
            f"""
            <div class="sidebar-brand">
                {logo_html}
                <div class="sidebar-brand-subline">financial consulting</div>
            </div>
            <div class="sidebar-section-title">Technische Prüfung</div>
            """,
            unsafe_allow_html=True,
        )

        check_xsd = st.checkbox(
            "Factur-X XML gegen XSD prüfen",
            value=True,
            help="Prüft die XML-Struktur mit der factur-x Library. Empfohlen.",
        )
        check_schematron = st.checkbox(
            "Schematron-Prüfung aktivieren",
            value=False,
            help=(
                "Strengere EN16931-Regelprüfung. Kann je nach Umgebung langsamer sein "
                "und zusätzliche Dependencies der factur-x Library nutzen."
            ),
        )

    return check_xsd, check_schematron


def _render_hero() -> None:
    logo_uri = _logo_src()
    logo_html = (
        f'<img class="brand-logo-img" src="{logo_uri}" alt="LENCO Logo" />'
        if logo_uri
        else ""
    )

    st.markdown(
        f"""
        <div class="hero-card">
            {logo_html}
            <h1 class="hero-title">.xml <span class="accent">-></span> ZUGFeRD converter</h1>
            <p class="hero-subtitle">
                XML-Rechnungen lokal prüfen, als klare PDF-Rechnung rendern
                und als Factur-X/XRechnung-PDF mit eingebetteter XML-Datei herunterladen.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _process_upload(file_name: str, file_bytes: bytes, check_xsd: bool, check_schematron: bool) -> None:
    # Privacy by design:
    # - no uploaded XML is written to disk
    # - no XML content is logged
    # - no database or persistent cache is used
    # - generated PDF/report are kept only in st.session_state for download
    warnings: list[str] = []
    report_context: dict[str, Any] = {
        "file_name": file_name,
        "detected_structure": {},
        "missing_required_fields": [],
        "validation_errors": [],
        "warnings": warnings,
        "agent_result": None,
    }

    try:
        xml_root = validate_xml_upload(file_name=file_name, data=file_bytes)
        # pretty_print_xml is intentionally called to verify serialization works.
        # Its raw content is not logged or displayed.
        _ = pretty_print_xml(xml_root)

        detected_structure = get_xml_structure_summary(xml_root)
        invoice_data = extract_invoice_data(xml_root)

        report_context["detected_structure"] = detected_structure

        missing_fields = validate_required_invoice_fields(invoice_data)
        if missing_fields:
            report_context["missing_required_fields"] = missing_fields
            st.session_state.status = "error"
            st.session_state.pdf_bytes = None
            st.session_state.report_text = build_conversion_report(
                status="nicht möglich",
                context=report_context,
                error_message=(
                    "Die Konvertierung wurde abgebrochen, weil Pflichtangaben "
                    "fehlen oder nicht sicher aus der XML-Datei gelesen werden konnten."
                ),
            )
            return

        requested_level = _detect_requested_level(invoice_data)
        if requested_level == "xrechnung":
            warnings.append(
                "XRECHNUNG-Profil erkannt. Die Original-XML wird fachlich unverändert verwendet, "
                "wenn sie als CII/Factur-X-Struktur erkannt wird. Falls die lokale factur-x Library "
                "keine dedizierte XRECHNUNG-Levelprüfung unterstützt, wird EN16931 als technische "
                "Näherung verwendet."
            )

        if is_likely_facturx_xml(xml_root):
            facturx_xml_bytes = file_bytes
            warnings.append(
                "Die hochgeladene XML wurde als Factur-X/CII bzw. XRechnung-CII erkannt "
                "und fachlich unverändert eingebettet."
            )
        else:
            facturx_xml_bytes = build_facturx_xml_from_invoice(invoice_data)
            warnings.append(
                "Die Eingabe-XML war keine erkennbare Factur-X/CII-Datei. "
                "Die Factur-X-XML wurde deterministisch aus den extrahierten Rechnungsdaten erzeugt."
            )

        xml_validation = validate_facturx_xml(
            facturx_xml_bytes,
            check_xsd=check_xsd,
            check_schematron=check_schematron,
            level=requested_level,
        )

        if xml_validation.get("warnings"):
            warnings.extend(xml_validation["warnings"])

        if not xml_validation["ok"]:
            report_context["validation_errors"].append(xml_validation["error"])
            st.session_state.status = "error"
            st.session_state.pdf_bytes = None
            st.session_state.report_text = build_conversion_report(
                status="nicht möglich",
                context=report_context,
                error_message="Die erzeugte oder gelieferte Factur-X-XML ist technisch nicht valide.",
            )
            return

        visible_pdf_bytes = render_invoice_pdf(invoice_data)
        final_pdf_bytes = embed_xml_in_pdf(
            pdf_bytes=visible_pdf_bytes,
            facturx_xml_bytes=facturx_xml_bytes,
            check_xsd=False,
            check_schematron=False,
            level=requested_level,
        )

        report_context["warnings"] = warnings
        status = "erfolgreich mit Warnungen" if warnings else "erfolgreich"

        st.session_state.status = "success"
        st.session_state.pdf_bytes = final_pdf_bytes
        st.session_state.report_text = build_conversion_report(
            status=status,
            context=report_context,
            error_message=None,
        )
        st.session_state.warnings = warnings

    except XMLValidationError as exc:
        report_context["validation_errors"].append(str(exc))
        st.session_state.status = "error"
        st.session_state.pdf_bytes = None
        st.session_state.report_text = build_conversion_report(
            status="nicht möglich",
            context=report_context,
            error_message=str(exc),
        )
    except FacturXBuildError as exc:
        report_context["validation_errors"].append(str(exc))
        st.session_state.status = "error"
        st.session_state.pdf_bytes = None
        st.session_state.report_text = build_conversion_report(
            status="nicht möglich",
            context=report_context,
            error_message=str(exc),
        )
    except Exception as exc:
        # Do not log XML contents. The traceback is intentionally content-free.
        technical_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        report_context["validation_errors"].append(technical_error)
        st.session_state.status = "error"
        st.session_state.pdf_bytes = None
        st.session_state.report_text = build_conversion_report(
            status="nicht möglich",
            context=report_context,
            error_message=technical_error,
        )



def _render_privacy_disclaimer() -> None:
    st.markdown(
        f"""
        <div class="privacy-disclaimer">
            <strong>Datenschutz- und Verarbeitungshinweis</strong><br/>
            {PRIVACY_DISCLAIMER}
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    _init_state()

    st.set_page_config(page_title=APP_TITLE, page_icon="📄", layout="wide")
    _inject_css()

    check_xsd, check_schematron = _render_sidebar()
    _render_hero()

    st.markdown(
        """
        <div class="neutral-note">
            Unterstützt werden typische Rechnungs-XMLs wie Factur-X/CII, ZUGFeRD,
            XRechnung-CII, UBL-nahe XMLs und viele ERP-XMLs über namespace-unabhängige Mappings.
            Nicht eindeutig erkennbare Pflichtdaten werden im Fehlerbericht ausgewiesen.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="upload-head-card">
            <div class="upload-title">XML-Rechnung hier hochladen</div>
            <div class="upload-caption">
                XML-Datei in das Feld ziehen oder Upload klicken. Die Konvertierung startet automatisch.
            </div>
            <div class="upload-arrow">↓</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader(
        "XML-Datei hier ablegen oder Upload klicken",
        type=["xml"],
        accept_multiple_files=False,
        label_visibility="collapsed",
    )

    if uploaded_file is None:
        st.markdown(
            '<div class="status-card status-neutral">Warte auf eine .xml-Datei.</div>',
            unsafe_allow_html=True,
        )
        _render_privacy_disclaimer()
        return

    file_bytes = uploaded_file.getvalue()
    current_hash = _uploaded_file_hash(uploaded_file.name, file_bytes)

    if st.session_state.file_hash != current_hash:
        st.session_state.file_hash = current_hash
        st.session_state.status = "processing"
        st.session_state.pdf_bytes = None
        st.session_state.report_text = None
        st.session_state.warnings = []

        with st.spinner("Verarbeite XML lokal und erstelle ZUGFeRD-PDF ..."):
            _process_upload(
                file_name=uploaded_file.name,
                file_bytes=file_bytes,
                check_xsd=check_xsd,
                check_schematron=check_schematron,
            )

    if st.session_state.status == "success":
        st.markdown(
            '<div class="status-card status-success">ZUGFeRD-/Factur-X-PDF wurde erstellt.</div>',
            unsafe_allow_html=True,
        )

        if st.session_state.warnings:
            with st.expander("Warnungen anzeigen"):
                for warning in st.session_state.warnings:
                    st.warning(warning)

        st.download_button(
            label="Fertige ZUGFeRD-PDF herunterladen",
            data=st.session_state.pdf_bytes,
            file_name=DOWNLOAD_PDF_NAME,
            mime="application/pdf",
            on_click=_clear_generated_session_data_after_download,
        )

        with st.expander("Technischen Prüfbericht anzeigen"):
            st.text(st.session_state.report_text or "")

    elif st.session_state.status == "downloaded":
        st.markdown(
            '<div class="status-card status-neutral">Download gestartet. Temporäre PDF- und Berichtsdaten wurden aus der Session entfernt.</div>',
            unsafe_allow_html=True,
        )

    elif st.session_state.status == "error":
        st.markdown(
            '<div class="status-card status-error">Die Konvertierung konnte nicht abgeschlossen werden.</div>',
            unsafe_allow_html=True,
        )
        st.download_button(
            label="Fehlerbericht herunterladen",
            data=st.session_state.report_text or "Kein Fehlerbericht vorhanden.",
            file_name=DOWNLOAD_REPORT_NAME,
            mime="text/plain",
        )

        with st.expander("Fehlerbericht anzeigen"):
            st.text(st.session_state.report_text or "")

    _render_privacy_disclaimer()


if __name__ == "__main__":
    main()
