<!-- Translation baseline: README.md. Keep capability descriptions and executable examples aligned. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — Retrouvez le texte de vos livres numérisés, pour le lire et le modifier." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="../../README_zh-CN.md">简体中文</a> | <a href="../zh-TW/README.md">繁體中文</a> | <a href="../ja/README.md">日本語</a> | <a href="../ko/README.md">한국어</a> | <a href="../ru/README.md">Русский</a> | <strong>Français</strong> | <a href="../es/README.md">Español</a> | <a href="../de/README.md">Deutsch</a> | <a href="../it/README.md">Italiano</a></p>
  <p>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/v/pdf-craft.svg?color=AD493B" alt="PyPI" /></a>
    <a href="https://pypi.org/project/pdf-craft/"><img src="https://img.shields.io/pypi/pyversions/pdf-craft.svg" alt="Python" /></a>
    <a href="https://github.com/oomol-lab/pdf-craft/actions/workflows/merge-build.yml"><img src="https://img.shields.io/github/actions/workflow/status/oomol-lab/pdf-craft/merge-build.yml" alt="CI" /></a>
    <a href="../../LICENSE"><img src="https://img.shields.io/github/license/oomol-lab/pdf-craft" alt="MIT" /></a>
  </p>
  <p>
    <a href="https://inkora.oomol.com/pdf-craft/"><strong>Essayer en ligne</strong></a> ·
    <a href="#quick-start"><strong>Démarrage Python</strong></a> ·
    <a href="#documentation"><strong>Documentation</strong></a>
  </p>
</div>

Convertissez des PDF numérisés en Markdown et EPUB, traduisez leur contenu et réinsérez le texte traduit dans un PDF.

## Des pages numérisées aux documents exploitables

PDF Craft est une bibliothèque Python pour les livres numérisés et les documents scientifiques ou techniques. Elle extrait le contenu des pages et organise le texte, les chapitres, la table des matières, les notes de bas de page, les tableaux, les formules et les images pour faciliter la lecture et la modification.

**Markdown : modifier, rechercher et traiter le contenu.**

![Exemple PDF vers Markdown en anglais](../images/pdf2md-en.png)

**EPUB : lire dans une application de lecture ou sur une liseuse.**

![Exemple PDF vers EPUB en anglais](../images/pdf2epub-en.png)

Le résultat dépend de la qualité du scan, de la mise en page et du modèle OCR. Vérifiez un document représentatif avant de traiter un ensemble de fichiers.

## Possibilités

| Objectif | Fonctionnalité |
| --- | --- |
| Modifier un livre numérisé | PDF → Markdown, avec texte et fichiers images |
| Lire sur une liseuse | PDF → EPUB, avec métadonnées et table des matières |
| Lire dans une autre langue | Traduction pendant la conversion ou d’un EPUB existant ; sortie traduite seule ou bilingue |
| Créer un PDF traduit | Traduction du texte extrait et réinsertion sur les pages d’origine |
| Intégrer la conversion dans une application | API Python et fichiers d’extraction réutilisables |

## Choisir son mode d’utilisation

| Mode | Pour qui | Prérequis |
| --- | --- | --- |
| **[En ligne](https://inkora.oomol.com/pdf-craft/)** | Découvrir le résultat | Un navigateur ; fonctionnalités et conditions précisées dans l’application en ligne |
| **Python + OCR distant** | Développeurs sans exécution locale des modèles | Python, Poppler, URL et identifiants d’un service compatible |
| **Python + OCR local** | Développeurs disposant d’un GPU NVIDIA | Python, Poppler, CUDA, VRAM suffisante et fichiers des modèles |

L’OCR distant envoie les pages au service configuré et ne nécessite pas CUDA en local. L’OCR local s’exécute sur votre machine ; si vous utilisez un LLM distant pour la traduction ou l’analyse de la table des matières, le contenu correspondant est tout de même envoyé à ce service.

<details>
<summary>Aperçu de l’application en ligne, en anglais</summary>

[![PDF Craft en ligne](../images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

</details>

<a id="quick-start"></a>

## Démarrage rapide

Cet exemple convertit un PDF en Markdown avec un OCR distant. Préparez **Python 3.11–3.13, Poppler et une configuration fonctionnelle de service compatible avec DeepSeek OCR**. Consultez le [guide d’installation](../en/INSTALLATION.md). Les guides détaillés liés depuis cette page sont en anglais.

### 1. Installer

```bash
python -m pip install pdf-craft
```

### 2. Convertir un PDF

Placez `input.pdf` dans le répertoire depuis lequel vous exécutez le script. Remplacez l’URL, la clé API et le nom du modèle par ceux de votre service :

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

`https://example.com/v1` est une adresse fictive. Utilisez un service compatible qui fournit réellement le modèle OCR. Pour les autres modèles, consultez la [configuration OCR](../en/OCR_BACKENDS.md).

Ouvrez ensuite `output.md`. Les documents contenant des images produisent également des fichiers de ressources : conservez-les avec le Markdown lors d’un déplacement ou d’un partage.

### 3. Créer un EPUB

Réutilisez l’instance `craft` configurée ci-dessus et remplacez la dernière ligne par :

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

Ouvrez `output.epub` dans un lecteur EPUB. Le titre, l’auteur et les options de rendu sont décrits dans [Conversion et traduction PDF](../en/PDF_TRANSLATION.md). En cas de problème, consultez le [dépannage](../en/TROUBLESHOOTING.md).

## Traduction et réutilisation de l’extraction

**Traduire des livres.** Fournissez un traducteur de chapitres lors de la conversion PDF vers Markdown ou EPUB, ou traduisez directement un EPUB existant. La traduction utilise un LLM textuel distinct ; OCR et traduction se configurent séparément. Pour l’EPUB, vous pouvez remplacer l’original ou ajouter la traduction pour une lecture bilingue.

**Créer un PDF traduit.** Extrayez le contenu, traduisez-le et réinsérez le texte sur les pages d’origine. Ce traitement nécessite aussi Ghostscript et des polices locales adaptées. Vérifiez la mise en page avec le texte original et sa traduction.

**Extraire une fois, réutiliser ensuite.** Enregistrez un fichier `.pcex` pour un rendu ultérieur, une traduction ou un traitement sur une autre machine. Réutilisez l’instance `craft` configurée :

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

Consultez [Conversion et traduction PDF](../en/PDF_TRANSLATION.md), [Traduction EPUB](../en/EPUB_TRANSLATION.md) et le [format `.pcex`](../en/PCEX_FORMAT.md).

## OCR et environnement requis

PDF Craft prend en charge **DeepSeek OCR, DeepSeek OCR 2 et Unlimited OCR**, chacun avec des configurations locales et distantes. L’installation standard convient à l’OCR distant. Pour l’OCR local, ajoutez les dépendances facultatives :

```bash
python -m pip install "pdf-craft[local]"
```

Il faut également une version de PyTorch compatible avec CUDA, suffisamment de VRAM et les fichiers des modèles. Ceux-ci sont téléchargés depuis Hugging Face par défaut ; vous pouvez les télécharger à l’avance et les charger localement. Les préréglages et besoins varient : consultez la [configuration OCR](../en/OCR_BACKENDS.md).

**La prise en charge des langues dépend de l’étape.** Les langues du README indiquent celles de la documentation. La reconnaissance dépend du modèle OCR ; la traduction, du traducteur et du LLM textuel. Le paramètre EPUB `lan` propose actuellement `zh` / `en` ; consultez la [référence API](../en/API_REFERENCE.md).

<a id="documentation"></a>

## Documentation

Les guides détaillés suivants sont en anglais.

| Besoin | Guide |
| --- | --- |
| Dépendances système et GPU local | [Installation](../en/INSTALLATION.md) |
| Modèles, services distants et cache | [Configuration OCR](../en/OCR_BACKENDS.md) |
| Conversion PDF, création EPUB et PDF traduit | [Conversion et traduction PDF](../en/PDF_TRANSLATION.md) |
| Traduction d’EPUB existants et sortie bilingue | [Traduction EPUB](../en/EPUB_TRANSLATION.md) |
| Paramètres, types et méthodes | [Référence API](../en/API_REFERENCE.md) |
| Stockage et échange des extractions | [Format `.pcex`](../en/PCEX_FORMAT.md) |
| Problèmes d’installation et de conversion | [Dépannage](../en/TROUBLESHOOTING.md) |

## Retours et contributions

Signalez les problèmes ou suggestions dans les [Issues](https://github.com/oomol-lab/pdf-craft/issues). Pour un problème de conversion, indiquez la version du paquet, le type de configuration OCR, les journaux d’erreur et un fichier minimal partageable publiquement. Retirez les identifiants et les données privées.

Les [Pull Requests](https://github.com/oomol-lab/pdf-craft/pulls) améliorant le code, la documentation et les traductions sont les bienvenues. Les fonctionnalités et exemples des README traduits doivent rester cohérents avec la version anglaise. Si PDF Craft vous aide, une Star permettra à d’autres de le découvrir.

## Projets associés

[Wiki Graph](https://github.com/oomol-lab/wiki-graph) transforme les livres EPUB ou Markdown obtenus en résumés structurés, en topologies de chapitres et en graphes de connaissances.

## Licence et remerciements

PDF Craft utilise la [licence MIT](../../LICENSE). Les dépendances tierces et les modèles OCR choisis conservent leurs propres licences.

Merci à [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) et aux autres projets open source.

<!-- community-footer:start -->

## Contributeurs

Merci à toutes les personnes qui contribuent à PDF Craft. Les améliorations du code, de la documentation et des traductions sont les bienvenues.

[![Contributeurs de PDF Craft](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="Évolution des étoiles de PDF Craft" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
