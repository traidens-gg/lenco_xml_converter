# LENCO .xml -> ZUGFeRD converter

Streamlit-App zur lokalen oder online gehosteten Verarbeitung von XML-Rechnungsdateien und zur Erzeugung einer ZUGFeRD-/Factur-X-PDF.

Diese Version nutzt **keinen KI-Agenten** und **keine OpenAI API**. Die Verarbeitung erfolgt lokal im Python-Prozess der Streamlit-App und ist deterministisch.

Die erzeugte Datei ist **keine reine XML-Datei**, sondern eine visuell lesbare PDF-Rechnung, in die eine strukturierte XML-Rechnung als `factur-x.xml` eingebettet wird.


## App-Layout und CI

Die Streamlit-Oberfläche nutzt die LENCO CI-Farben:

- Bordeaux `#8A2B2B` für Akzente, Call-to-Actions und Statusflächen,
- Anthrazit `#555555` für Schrift, Headlines und UI-Struktur,
- helle Grautöne und Weiß für lesbare Dashboard-Flächen,
- serifenlose Typografie.

Der Markenname wird in der Oberfläche als `lenco` dargestellt, mit grauem `len` und bordeauxfarbenem `co`.

Das Layout ist zentriert aufgebaut. Der Upload-Bereich ist als prominenter Drop-Bereich gestaltet, damit Nutzer sofort erkennen, wo die XML-Datei abgelegt oder ausgewählt werden muss. Erfolgsstatus wird hellgrün dargestellt.

## XML-Abdeckung

Die App ist so erweitert, dass sie nicht nur ein einzelnes XML-Schema erwartet. Sie arbeitet namespace-unabhängig und unterstützt typische Rechnungs-XMLs wie:

- Factur-X / ZUGFeRD im UN/CEFACT-CII-Format,
- XRechnung-CII,
- UBL-nahe Rechnungen,
- viele ERP-Export-XMLs mit deutschen oder englischen Feldnamen,
- generische XML-Rechnungen über heuristische Feldsuche.

Wichtig: XML ist kein Rechnungsstandard, sondern ein Datenformat. Daher kann keine App seriös garantieren, jedes beliebige XML-Schema vollständig zu verstehen. Wenn Pflichtfelder nicht eindeutig gefunden werden, erfindet die App keine Werte, sondern erstellt einen Fehlerbericht mit den fehlenden Feldern.

## Projektstruktur

```text
zugferd_converter_app/
  app.py
  agent.py
  xml_utils.py
  pdf_renderer.py
  zugferd_builder.py
  requirements.txt
  packages.txt
  runtime.txt
  Dockerfile
  .dockerignore
  .gitignore
  .streamlit/
    config.toml
    secrets.toml.example
  .env.example
  README.md
```

## Lokale Installation

```bash
cd zugferd_converter_app
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```


## Lokales Troubleshooting

Wenn Streamlit im Terminal startet, aber kein Browserfenster aufgeht, öffne manuell:

```text
http://localhost:8501
```

Die lokale `.streamlit/config.toml` setzt bewusst kein `server.headless = true`, damit Streamlit auf dem lokalen Rechner wieder automatisch den Browser öffnen kann. Für Docker wird Headless-Modus im `Dockerfile` separat gesetzt.

## Lokal starten

```bash
streamlit run app.py
```

Es ist kein `OPENAI_API_KEY` erforderlich.

## Online hosten mit Streamlit Community Cloud

Empfohlen für einen schnellen Start:

1. Projekt in ein GitHub-Repository hochladen.
2. Sicherstellen, dass mindestens diese Dateien im Repository liegen:
   - `app.py`
   - `requirements.txt`
   - `packages.txt`
   - `runtime.txt`
   - `.streamlit/config.toml`
   - alle Python-Module der App
3. Bei Streamlit Community Cloud anmelden.
4. Neues App-Deployment erstellen.
5. Repository auswählen.
6. Main file path auf `app.py` setzen.
7. Deploy starten.

Da diese Version keine API-Keys nutzt, müssen in Streamlit Cloud keine Secrets hinterlegt werden.

## Online hosten per Docker

Alternativ kann die App als Container betrieben werden:

```bash
docker build -t lenco-zugferd-converter .
docker run -p 8501:8501 lenco-zugferd-converter
```

Danach im Browser öffnen:

```text
http://localhost:8501
```

Der Dockerfile ist geeignet als Basis für Render, Railway, Fly.io, Azure Container Apps, Google Cloud Run oder eigene Server.


## PDF-Layout

Die erzeugte PDF-Rechnung verwendet ein neutrales Layout:

- kein sichtbarer LENCO-Name in der PDF,
- neutraler Dokumenttitel `RECHNUNG`,
- Untertitel `Factur-X/XRechnung-XML`,
- Tabellen mit automatischem Zeilenumbruch,
- Rechnungsdatenblock über volle Seitenbreite, damit lange Referenzen nicht überlappen,
- keine Schlussformulierung wie „menschenlesbare Rechnung“.

## Workflow

1. Upload genau einer `.xml`-Rechnungsdatei.
2. Syntaktische XML-Prüfung mit `lxml`.
3. Namespace-unabhängige lokale Extraktion zentraler Rechnungsdaten:
   - Rechnungsnummer
   - Rechnungsdatum
   - Fälligkeitsdatum
   - Verkäufer
   - Käufer
   - BuyerReference / Leitweg-ID
   - Positionen
   - Steuerdaten
   - Summen
   - Zahlungsbedingungen
   - Bankdaten
4. Prüfung der Pflichtfelder.
5. Erstellung einer sichtbaren PDF-Rechnung mit ReportLab.
6. Wenn die XML bereits Factur-X/CII bzw. XRechnung-CII ist:
   - fachlich unveränderte Einbettung der Original-XML.
7. Wenn die XML keine erkennbare CII-/Factur-X-Struktur ist:
   - deterministische Erzeugung einer Factur-X/CII-XML aus den extrahierten Daten.
8. Einbettung der XML in die PDF mit der Python-Library `factur-x`.
9. Download als `zugferd_invoice.pdf`.

Bei Fehlern erzeugt die App `conversion_report.txt`.


## Datenschutz / keine dauerhafte Speicherung

Die App ist so gebaut, dass bei der Konvertierung keine Rechnungsdaten dauerhaft gespeichert werden:

- hochgeladene XML-Dateien werden nicht auf Festplatte geschrieben,
- es wird keine Datenbank verwendet,
- es wird kein persistenter Cache verwendet,
- XML-Inhalte werden nicht in Logs ausgegeben,
- es erfolgt keine Übermittlung an OpenAI, KI-Dienste oder sonstige Drittanbieter,
- die erzeugte PDF und der technische Bericht werden nur temporär im Arbeitsspeicher der aktuellen Streamlit-Sitzung gehalten, damit der Nutzer sie herunterladen kann,
- über den Button `Sitzungsdaten löschen` können diese temporären Sitzungsdaten direkt entfernt werden.

Für produktives Hosting sollte zusätzlich ein Zugriffsschutz vorgesehen werden, weil Rechnungen personenbezogene oder vertrauliche Geschäftsdaten enthalten können.

## Datenschutz beim Online-Hosting

Rechnungen enthalten sensible Daten. Für produktive Nutzung sollte die App nicht öffentlich ohne Zugriffsschutz betrieben werden. Sinnvolle Maßnahmen:

- Deployment nur privat oder intern zugänglich machen,
- HTTPS nutzen,
- keine XML-Inhalte loggen,
- Uploads nicht dauerhaft speichern,
- externe Validatoren nur verwenden, wenn Datenschutz und Auftragsverarbeitung geklärt sind.

Diese App speichert Uploads nicht dauerhaft und gibt keine sensiblen XML-Inhalte in Logs aus.

## Fachlicher Hinweis

Eine echte ZUGFeRD-/Factur-X-Rechnung besteht aus:

- einer visuell lesbaren Rechnung als PDF,
- einer eingebetteten strukturierten XML-Rechnung,
- passenden PDF/A-3- und Factur-X-/ZUGFeRD-Metadaten.

Die App nutzt `factur-x`, um PDF und XML zu verbinden. Wenn die XML bereits eine XRechnung/CII ist, wird sie fachlich unverändert eingebettet. Wenn eine echte Profiltransformation nötig wäre, wird diese nicht stillschweigend simuliert.

## Keine erfundenen Rechnungsdaten

Diese Version erfindet keine fehlenden Rechnungsdaten. Wenn zentrale Pflichtdaten nicht lokal gefunden werden können, bricht die App ab und erstellt einen Fehlerbericht.

## Validierung

Die App prüft mindestens:

- Dateiendung `.xml`,
- Dateigröße,
- wohlgeformtes XML,
- zentrale Pflichtfelder,
- Erzeugbarkeit einer sichtbaren PDF,
- technische Einbettung der XML in die PDF,
- optional XSD-Prüfung der Factur-X-XML,
- optional Schematron-Prüfung.

## Externe Compliance-Prüfung

Für produktive Nutzung sollte jede erzeugte Datei zusätzlich mit externen Validatoren geprüft werden:

- veraPDF für PDF/A-3,
- ZUGFeRD-/Factur-X-Validator,
- KoSIT-/XRechnung-Validator für XRechnung/EN16931.

Das ist wichtig, weil rechtliche und technische Konformität nicht allein durch die Erzeugung einer PDF mit eingebetteter XML garantiert ist.

## Erweiterbarkeit

Die App ist modular aufgebaut. Weitere Eingabeformate können später ergänzt werden:

- weitere ERP-XML-Mappings,
- CSV,
- JSON,
- API-basierte Rechnungsdaten,
- externe Validatoren,
- Authentifizierung für Online-Betrieb.
