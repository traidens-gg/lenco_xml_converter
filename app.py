from __future__ import annotations

import hashlib
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


def _clear_session_data() -> None:
    """Remove temporary conversion data from the current Streamlit session.

    The app never writes uploaded XML or generated documents to persistent
    storage. This function clears the in-memory objects that are kept only so
    the user can download the generated PDF or error report.
    """
    st.session_state.file_hash = None
    st.session_state.status = "idle"
    st.session_state.pdf_bytes = None
    st.session_state.report_text = None
    st.session_state.warnings = []


def _uploaded_file_hash(file_name: str, data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(file_name.encode("utf-8", errors="ignore"))
    digest.update(data)
    return digest.hexdigest()


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
                    radial-gradient(circle at 50% 0%, rgba(138, 43, 43, 0.10), transparent 34%),
                    linear-gradient(180deg, #FBFAF8 0%, var(--lenco-bg) 100%);
                color: var(--lenco-text);
                font-family: Arial, Helvetica, sans-serif;
            }

            .block-container {
                max-width: 940px;
                padding-top: 2.4rem;
                padding-bottom: 3rem;
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
                padding: 0.35rem 0 1.2rem 0;
                border-bottom: 1px solid var(--lenco-line);
                margin-bottom: 1.2rem;
                text-align: center;
            }

            .sidebar-brand-logo {
                font-size: 2.2rem;
                line-height: 1;
                font-weight: 800;
                letter-spacing: -0.07em;
            }

            .brand-len {
                color: var(--lenco-anthracite);
            }

            .brand-co {
                color: var(--lenco-bordeaux);
            }

            .sidebar-brand-subline {
                margin-top: 0.35rem;
                color: var(--lenco-muted);
                font-size: 0.78rem;
                letter-spacing: 0.14em;
                text-transform: uppercase;
            }

            .sidebar-section-title {
                margin: 0 0 0.65rem 0;
                color: var(--lenco-anthracite);
                font-weight: 700;
                font-size: 0.95rem;
                text-align: center;
            }

            .hero-card {
                background: var(--lenco-card);
                border: 1px solid var(--lenco-line);
                border-radius: 28px;
                padding: 2.35rem 2.25rem 2.1rem 2.25rem;
                box-shadow: 0 22px 55px rgba(85, 85, 85, 0.10);
                margin: 0 auto 1.25rem auto;
                position: relative;
                overflow: hidden;
                text-align: center;
            }

            .hero-card:before {
                content: "";
                position: absolute;
                top: 0;
                left: 0;
                width: 7px;
                height: 100%;
                background: var(--lenco-bordeaux);
            }

            .brand-wordmark {
                font-size: 2.25rem;
                font-weight: 800;
                letter-spacing: -0.075em;
                line-height: 1;
                margin-bottom: 1.05rem;
                text-align: center;
            }

            .hero-title {
                color: var(--lenco-anthracite);
                font-size: clamp(2rem, 4vw, 3.15rem);
                font-weight: 800;
                letter-spacing: -0.045em;
                line-height: 1.04;
                margin: 0 auto;
                text-align: center;
            }

            .hero-title .accent {
                color: var(--lenco-bordeaux);
            }

            .hero-subtitle {
                color: var(--lenco-muted);
                font-size: 1.02rem;
                line-height: 1.55;
                max-width: 760px;
                margin: 1.05rem auto 0 auto;
                text-align: center;
            }

            .neutral-note {
                background: #FFFFFF;
                border: 1px solid var(--lenco-line);
                border-left: 5px solid var(--lenco-bordeaux);
                border-radius: 18px;
                padding: 1rem 1.1rem;
                color: var(--lenco-muted);
                line-height: 1.5;
                margin: 0 auto 1.35rem auto;
                text-align: center;
                box-shadow: 0 10px 24px rgba(85, 85, 85, 0.04);
            }

            .upload-head-card {
                background: var(--lenco-card);
                border: 1px solid var(--lenco-line);
                border-radius: 24px;
                padding: 1.45rem 1.4rem 1.25rem 1.4rem;
                box-shadow: 0 14px 35px rgba(85, 85, 85, 0.07);
                margin: 1rem auto 1rem auto;
                text-align: center;
            }

            .upload-title {
                color: var(--lenco-anthracite);
                font-weight: 800;
                font-size: 1.25rem;
                margin: 0 0 0.35rem 0;
                text-align: center;
            }

            .upload-caption {
                color: var(--lenco-muted);
                font-size: 0.98rem;
                margin: 0 auto;
                max-width: 650px;
                text-align: center;
                line-height: 1.5;
            }

            .upload-arrow {
                width: 52px;
                height: 52px;
                margin: 1rem auto 0 auto;
                border-radius: 50%;
                background: rgba(138, 43, 43, 0.10);
                border: 1px solid rgba(138, 43, 43, 0.25);
                display: flex;
                align-items: center;
                justify-content: center;
                color: var(--lenco-bordeaux);
                font-size: 1.75rem;
                font-weight: 900;
            }

            div[data-testid="stFileUploader"] {
                max-width: 880px;
                margin-left: auto;
                margin-right: auto;
            }

            div[data-testid="stFileUploader"] section {
                border: 2px dashed rgba(138, 43, 43, 0.45) !important;
                background:
                    linear-gradient(180deg, rgba(138, 43, 43, 0.035), rgba(255, 255, 255, 0.92)),
                    #FFFFFF !important;
                border-radius: 24px !important;
                min-height: 172px;
                padding: 2rem 1.4rem !important;
                box-shadow: 0 16px 38px rgba(138, 43, 43, 0.08);
                display: flex;
                align-items: center;
                justify-content: center;
                text-align: center;
            }

            div[data-testid="stFileUploader"] section:hover {
                border-color: var(--lenco-bordeaux) !important;
                background:
                    linear-gradient(180deg, rgba(138, 43, 43, 0.06), rgba(255, 255, 255, 0.96)),
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
                font-size: 0.95rem !important;
            }

            div[data-testid="stFileUploader"] button {
                background: var(--lenco-bordeaux) !important;
                color: #FFFFFF !important;
                border: 1px solid var(--lenco-bordeaux) !important;
                border-radius: 14px !important;
                padding: 0.75rem 1.25rem !important;
                font-weight: 800 !important;
                box-shadow: 0 12px 24px rgba(138, 43, 43, 0.18);
            }

            .status-card {
                border-radius: 18px;
                padding: 1rem 1.15rem;
                margin: 1rem auto;
                border: 1px solid transparent;
                font-weight: 700;
                text-align: center;
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

            .privacy-disclaimer {
                max-width: 880px;
                margin: 1.6rem auto 0 auto;
                background: #FFFFFF;
                border: 1px solid var(--lenco-line);
                border-radius: 18px;
                padding: 1rem 1.15rem;
                color: var(--lenco-muted);
                font-size: 0.92rem;
                line-height: 1.55;
                box-shadow: 0 10px 24px rgba(85, 85, 85, 0.04);
                text-align: left;
            }

            .privacy-disclaimer strong {
                color: var(--lenco-anthracite);
            }

            .cleanup-area {
                max-width: 880px;
                margin: 0.8rem auto 0 auto;
                text-align: center;
            }

            div[data-testid="stDownloadButton"] {
                text-align: center;
            }

            div[data-testid="stDownloadButton"] button,
            div.stButton > button {
                background: var(--lenco-bordeaux);
                color: #FFFFFF;
                border: 1px solid var(--lenco-bordeaux);
                border-radius: 14px;
                padding: 0.72rem 1rem;
                font-weight: 750;
                box-shadow: 0 12px 25px rgba(138, 43, 43, 0.18);
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
                border-radius: 16px;
                background: #FFFFFF;
                box-shadow: 0 10px 24px rgba(85, 85, 85, 0.05);
                overflow: hidden;
                max-width: 880px;
                margin-left: auto;
                margin-right: auto;
            }

            div[data-testid="stAlert"] {
                border-radius: 14px;
                border: 1px solid rgba(138, 43, 43, 0.18);
                max-width: 880px;
                margin-left: auto;
                margin-right: auto;
            }

            .stCheckbox label {
                font-weight: 600;
            }

            @media (max-width: 760px) {
                .block-container {
                    padding-top: 1.2rem;
                }

                .hero-card {
                    padding: 1.55rem 1.35rem 1.25rem 1.35rem;
                    border-radius: 22px;
                }

                .brand-wordmark {
                    font-size: 1.8rem;
                }

                div[data-testid="stFileUploader"] section {
                    min-height: 150px;
                    padding: 1.5rem 1rem !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> tuple[bool, bool]:
    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-brand">
                <div class="sidebar-brand-logo"><span class="brand-len">len</span><span class="brand-co">co</span></div>
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
    st.markdown(
        """
        <div class="hero-card">
            <div class="brand-wordmark"><span class="brand-len">len</span><span class="brand-co">co</span></div>
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
                Ziehe deine XML-Datei in das Upload-Feld oder klicke auf den Upload-Button.
                Danach startet die Konvertierung automatisch.
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
        st.info("Warte auf eine `.xml`-Datei.")
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
        )

        with st.expander("Technischen Prüfbericht anzeigen"):
            st.text(st.session_state.report_text or "")

        st.markdown('<div class="cleanup-area">', unsafe_allow_html=True)
        if st.button("Sitzungsdaten löschen", key="clear_success"):
            _clear_session_data()
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

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

        st.markdown('<div class="cleanup-area">', unsafe_allow_html=True)
        if st.button("Sitzungsdaten löschen", key="clear_error"):
            _clear_session_data()
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    _render_privacy_disclaimer()


if __name__ == "__main__":
    main()
