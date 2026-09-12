<!-- Translation baseline: README.md. Keep capability descriptions and executable examples aligned. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — Gescannte Bücher wieder als lesbaren und bearbeitbaren Text nutzen." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="../../README_zh-CN.md">简体中文</a> | <a href="../zh-TW/README.md">繁體中文</a> | <a href="../ja/README.md">日本語</a> | <a href="../ko/README.md">한국어</a> | <a href="../ru/README.md">Русский</a> | <a href="../fr/README.md">Français</a> | <a href="../es/README.md">Español</a> | <strong>Deutsch</strong> | <a href="../it/README.md">Italiano</a></p>
  <p>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/v/pdf-craft.svg?color=AD493B" alt="PyPI" /></a>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="Python" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/merge-build.yml"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/merge-build.yml" alt="CI" /></a>
    <a href="../../LICENSE"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt="MIT" /></a>
  </p>
  <p>
    <a href="https://trendshift.io/repositories/15538"><img src="https://trendshift.io/api/badge/repositories/15538" alt="PDF Craft | GitHub Trending on Trendshift" width="250" height="55" /></a>
  </p>
  <p>
    <a href="https://inkora.oomol.com/pdf-craft/"><strong>Online ausprobieren</strong></a> ·
    <a href="#quick-start"><strong>Python-Schnellstart</strong></a> ·
    <a href="#documentation"><strong>Dokumentation</strong></a>
  </p>
</div>

Gescannte PDFs in Markdown und EPUB umwandeln, Inhalte übersetzen und Übersetzungen als PDF ausgeben.

## Von gescannten Seiten zu nutzbaren Dokumenten

PDF Craft ist eine Python-Bibliothek für gescannte Bücher sowie wissenschaftliche und technische Dokumente. Sie extrahiert Seiteninhalte und strukturiert Fließtext, Kapitel, Inhaltsverzeichnisse, Fußnoten, Tabellen, Formeln und Bilder für die weitere Bearbeitung und zum Lesen.

**Markdown: Inhalte bearbeiten, durchsuchen und weiterverarbeiten.**

![Beispiel PDF zu Markdown auf Englisch](../images/pdf2md-en.png)

**EPUB: Bücher in einem E-Book-Reader lesen.**

![Beispiel PDF zu EPUB auf Englisch](../images/pdf2epub-en.png)

Das Ergebnis hängt von Scanqualität, Seitenlayout und OCR-Modell ab. Prüfe ein repräsentatives Dokument, bevor du eine größere Sammlung verarbeitest.

## Funktionen

| Dein Ziel | PDF Craft bietet |
| --- | --- |
| Gescannte Bücher bearbeiten | PDF → Markdown mit Text und Bilddateien |
| In einem E-Book-Reader lesen | PDF → EPUB mit Buchmetadaten und Inhaltsverzeichnis |
| Bücher in einer anderen Sprache lesen | Übersetzung während der Konvertierung oder eines vorhandenen EPUB; rein übersetzte und zweisprachige Ausgabe |
| Eine übersetzte PDF erstellen | Extrahierten Text übersetzen und auf die ursprünglichen Seiten zurückschreiben |
| Konvertierung in eine Anwendung integrieren | Python-APIs und wiederverwendbare Extraktionsdateien für spätere Ausgabe oder Übersetzung |

## Nutzungsart wählen

| Variante | Geeignet für | Voraussetzungen |
| --- | --- | --- |
| **[Online](https://inkora.oomol.com/pdf-craft/)** | Einen ersten Test | Browser; Funktionen und Nutzungsbedingungen stehen in der Online-Anwendung |
| **Python + Remote-OCR** | Entwickler ohne lokale Ausführung der OCR-Modelle | Python, Poppler, URL und Zugangsdaten eines kompatiblen OCR-Dienstes |
| **Python + lokale OCR** | Entwickler mit eigener NVIDIA-GPU | Python, Poppler, CUDA, ausreichend VRAM und Modelldateien |

Remote-OCR sendet Seiten an den konfigurierten Dienst und benötigt lokal kein CUDA. Lokale OCR läuft auf deinem Rechner. Wenn du ein entferntes LLM für Übersetzung oder Inhaltsverzeichnisanalyse verwendest, werden die betreffenden Inhalte dennoch an diesen Dienst gesendet.

**Vorschau der Online-Anwendung auf Englisch**

[![PDF Craft Online](../images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

<a id="quick-start"></a>

## Schnellstart

Dieses Beispiel konvertiert eine PDF mit Remote-OCR in Markdown. Du brauchst **Python 3.11–3.13, Poppler und eine funktionierende Konfiguration für einen DeepSeek-OCR-kompatiblen Dienst**. Die [Installationsanleitung](../en/INSTALLATION.md) beschreibt die Einrichtung von Poppler. Die hier verlinkten ausführlichen Anleitungen sind auf Englisch.

### 1. Installieren

```bash
python -m pip install pdf-craft
```

### 2. PDF konvertieren

Lege `input.pdf` in das Verzeichnis, aus dem du das Skript ausführst. Ersetze URL, API-Schlüssel und Modellname durch die Angaben deines Dienstes:

```python
from pdf_craft import DeepSeekOCRVendorConfig, PDFCraft, PDFOptions

craft = PDFCraft(
    pdf=PDFOptions(
        ocr=DeepSeekOCRVendorConfig(
            base_url="https://example.com/v1",
            api_key="your-api-key",
            model="deepseek-ocr",
        ),
    ),
)

craft.convert_pdf_to_markdown("input.pdf", "output.md")
```

`https://example.com/v1` ist ein Platzhalter, kein funktionierender Endpunkt. Verwende einen kompatiblen Dienst, der das OCR-Modell tatsächlich bereitstellt. Andere Modelle findest du unter [OCR-Konfiguration](../en/OCR_BACKENDS.md).

Öffne anschließend `output.md`. Dokumente mit Bildern erzeugen zusätzliche Ressourcendateien. Behalte diese beim Verschieben oder Teilen zusammen mit der Markdown-Datei.

### 3. EPUB erstellen

Verwende die oben konfigurierte `craft`-Instanz und ersetze die letzte Zeile durch:

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

Öffne `output.epub` in einem EPUB-Reader. Titel, Autor und Ausgabeoptionen sind unter [PDF-Konvertierung und -Übersetzung](../en/PDF_TRANSLATION.md) beschrieben. Bei Installations- oder Laufzeitproblemen hilft die [Fehlerbehebung](../en/TROUBLESHOOTING.md).

## Übersetzung und Wiederverwendung der Extraktion

**Bücher übersetzen.** Übergib bei der Konvertierung von PDF in Markdown oder EPUB einen Kapitelübersetzer oder übersetze ein vorhandenes EPUB direkt. Die Übersetzung verwendet ein separates Text-LLM; OCR und Übersetzung werden unabhängig konfiguriert. Bei EPUB kannst du den Originaltext ersetzen oder die Übersetzung zum zweisprachigen Lesen ergänzen.

**Übersetzte PDF erstellen.** Extrahiere und übersetze den Inhalt und schreibe die Übersetzung auf die ursprünglichen Seiten zurück. Dafür werden zusätzlich Ghostscript und geeignete lokale Schriftarten benötigt. Prüfe das Layout anhand von Original und Übersetzung.

**Einmal extrahieren, später wiederverwenden.** Speichere eine `.pcex`-Datei für spätere Ausgabe, Übersetzung oder Verarbeitung auf einem anderen Rechner. Verwende die konfigurierte `craft`-Instanz:

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

Siehe [PDF-Konvertierung und -Übersetzung](../en/PDF_TRANSLATION.md), [EPUB-Übersetzung](../en/EPUB_TRANSLATION.md) und die [Referenz zum `.pcex`-Format](../en/PCEX_FORMAT.md).

## OCR und Laufzeitvoraussetzungen

PDF Craft unterstützt **DeepSeek OCR, DeepSeek OCR 2 und Unlimited OCR**, jeweils mit lokaler und entfernter Konfiguration.

Die Standardinstallation unterstützt Remote-OCR. Installiere für lokale OCR die zusätzlichen Abhängigkeiten:

```bash
python -m pip install "pdf-craft[local]"
```

Für die lokale Ausführung brauchst du außerdem eine passende PyTorch-Version mit CUDA, ausreichend VRAM und Modelldateien. Modelle werden standardmäßig von Hugging Face heruntergeladen. Du kannst sie vorab herunterladen und lokal laden. Voreinstellungen und Anforderungen unterscheiden sich je nach Modell; siehe [OCR-Konfiguration](../en/OCR_BACKENDS.md).

**Die Sprachunterstützung hängt vom Verarbeitungsschritt ab.** Die README-Sprachen geben die verfügbaren Dokumentationssprachen an. Texterkennung hängt vom OCR-Modell ab, Übersetzung vom Übersetzer und Text-LLM. Der EPUB-Parameter `lan` bietet derzeit `zh` / `en`; siehe [API-Referenz](../en/API_REFERENCE.md).

<a id="documentation"></a>

## Dokumentation

Die folgenden ausführlichen Anleitungen sind auf Englisch.

| Aufgabe | Anleitung |
| --- | --- |
| Systemabhängigkeiten installieren und lokale GPU einrichten | [Installation](../en/INSTALLATION.md) |
| OCR-Modelle, entfernte Dienste und Modellcache konfigurieren | [OCR-Konfiguration](../en/OCR_BACKENDS.md) |
| PDFs konvertieren, EPUBs erstellen und übersetzte PDFs ausgeben | [PDF-Konvertierung und -Übersetzung](../en/PDF_TRANSLATION.md) |
| Vorhandene EPUBs übersetzen und zweisprachige Ausgabe konfigurieren | [EPUB-Übersetzung](../en/EPUB_TRANSLATION.md) |
| Parameter, Typen und Methoden nachschlagen | [API-Referenz](../en/API_REFERENCE.md) |
| Extraktionsergebnisse speichern oder austauschen | [`.pcex`-Format](../en/PCEX_FORMAT.md) |
| Installations- und Konvertierungsprobleme lösen | [Fehlerbehebung](../en/TROUBLESHOOTING.md) |

## Rückmeldungen und Beiträge

Melde Probleme oder Verbesserungsvorschläge über [Issues](https://github.com/oomol-lab/pdf-craft/issues). Gib bei Konvertierungsproblemen die Paketversion, den OCR-Konfigurationstyp, Fehlerprotokolle und eine minimale, öffentlich teilbare Beispieldatei an. Entferne vorher Zugangsdaten und private Inhalte.

[Pull Requests](https://github.com/oomol-lab/pdf-craft/pulls) zur Verbesserung von Code, Dokumentation und Übersetzungen sind willkommen. Halte Funktionsbeschreibungen und Beispiele übersetzter READMEs mit der englischen Version synchron.

Wenn PDF Craft dir hilft, macht ein Star das Projekt für andere sichtbarer.

## Verwandte Projekte

[Wiki Graph](https://github.com/oomol-lab/wiki-graph) erstellt aus konvertierten EPUB- oder Markdown-Büchern strukturierte Zusammenfassungen, Kapiteltopologien und Wissensgraphen.

## Lizenz und Danksagung

PDF Craft verwendet die [MIT-Lizenz](../../LICENSE). Für Abhängigkeiten Dritter und ausgewählte OCR-Modelle gelten deren eigene Lizenzen.

Vielen Dank an [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) und die weiteren Open-Source-Projekte, die PDF Craft ermöglichen.

<!-- community-footer:start -->

## Mitwirkende

Vielen Dank an alle, die zu PDF Craft beitragen. Verbesserungen an Code, Dokumentation und Übersetzungen sind willkommen.

[![Mitwirkende bei PDF Craft](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="Entwicklung der Sterne von PDF Craft" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
