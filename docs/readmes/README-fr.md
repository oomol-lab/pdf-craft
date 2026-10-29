<!-- Content baseline: README-zh-CN.md. Preserve the Chinese README priorities in natural local copy; keep API examples accurate. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — Retrouvez le texte de vos livres numérisés, pour le lire et le modifier." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="README-zh-CN.md">简体中文</a> | <a href="README-zh-TW.md">繁體中文</a> | <a href="README-ja.md">日本語</a> | <a href="README-ko.md">한국어</a> | <a href="README-ru.md">Русский</a> | <strong>Français</strong> | <a href="README-es.md">Español</a> | <a href="README-de.md">Deutsch</a> | <a href="README-it.md">Italiano</a></p>
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
    <a href="https://pdfcraft.ai/pdf-craft/"><strong>Convertir un PDF en ligne</strong></a> ·
    <a href="#quick-start"><strong>Démarrage Python</strong></a> ·
    <a href="#documentation"><strong>Documentation</strong></a>
  </p>
</div>

PDF Craft est une bibliothèque Python conçue pour les livres numérisés, les articles scientifiques et les documents techniques. Elle transforme les PDF numérisés en Markdown facile à modifier ou en EPUB adapté à la lecture. Elle peut aussi traduire le contenu de PDF et d’EPUB dans une autre langue, créer des EPUB traduits ou bilingues et réinsérer la traduction sur les pages du PDF d’origine. Texte, chapitres, table des matières, notes de bas de page, tableaux, formules et images sont reconnus pour rendre le contenu consultable, modifiable et lisible au-delà des pages scannées.

## Convertissez vos fichiers en ligne, sans rien installer

Vous avez un PDF numérisé à modifier en Markdown ou à lire en EPUB ? Avec **[PDF Craft Online · pdfcraft.ai](https://pdfcraft.ai/pdf-craft/)**, importez votre fichier dans le navigateur, lancez la conversion, puis téléchargez le résultat une fois le traitement terminé. Pas de Python à installer, de CUDA à configurer, ni de service OCR ou de clé API à prévoir.

PDF Craft Online est le service en ligne officiel de ce projet open source. Utilisez-le pour convertir directement vos fichiers, ou choisissez la bibliothèque Python ci-dessous pour intégrer la conversion à votre application ou exécuter l’OCR sur votre ordinateur.

**[Importez votre PDF et lancez la conversion →](https://pdfcraft.ai/pdf-craft/)**

[![Interface de conversion PDF Craft Online, en anglais — cliquez pour importer un PDF](../images/website-en.png)](https://pdfcraft.ai/pdf-craft/)

## Exemples de conversion

**Markdown : modifier, rechercher et traiter le contenu.**

![Exemple PDF vers Markdown en anglais](../images/pdf2md-en.png)

**EPUB : lire dans une application de lecture ou sur une liseuse.**

![Exemple PDF vers EPUB en anglais](../images/pdf2epub-en.png)

Le résultat dépend de la qualité du scan, de la mise en page et du modèle OCR. Vérifiez un document représentatif avant de traiter un ensemble de fichiers.

**Quel résultat obtiendrez-vous avec votre fichier ? [Importez un PDF et convertissez-le en Markdown ou EPUB →](https://pdfcraft.ai/pdf-craft/)**

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
| **[Conversion en ligne](https://pdfcraft.ai/pdf-craft/)** | Importer un fichier et télécharger du Markdown ou un EPUB | Un navigateur ; aucune installation Python ni configuration OCR |
| **Python + OCR distant** | Développeurs sans exécution locale des modèles | Python, Poppler, URL et identifiants d’un service compatible |
| **Python + OCR local** | Développeurs disposant d’un GPU NVIDIA | Python, Poppler, CUDA, VRAM suffisante et fichiers des modèles |

L’OCR distant envoie les pages au service configuré et ne nécessite pas CUDA en local. L’OCR local s’exécute sur votre machine ; si vous utilisez un LLM distant pour la traduction ou l’analyse de la table des matières, le contenu correspondant est tout de même envoyé à ce service.

<a id="quick-start"></a>

## Démarrage rapide avec Python

Cet exemple convertit un PDF en Markdown avec un OCR distant. Préparez **Python 3.11–3.13, Poppler et une configuration fonctionnelle de service compatible avec DeepSeek OCR**. Consultez le [guide d’installation](../en/INSTALLATION.md). Les guides détaillés liés depuis cette page sont en anglais.

> Vous souhaitez simplement convertir un fichier, sans préparer d’environnement de développement ni de service OCR ? [Utilisez PDF Craft Online](https://pdfcraft.ai/pdf-craft/) et importez votre PDF dans le navigateur.

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

`https://example.com/v1` est une adresse fictive. Utilisez un service compatible qui fournit réellement le modèle OCR. Pour les autres modèles, consultez la [configuration OCR](../en/OCR_BACKENDS.md). Si vous n’avez pas encore de service OCR, vous pouvez convertir vos fichiers avec [PDF Craft Online](https://pdfcraft.ai/pdf-craft/).

Ouvrez ensuite `output.md`. Les documents contenant des images produisent également des fichiers de ressources : conservez-les avec le Markdown lors d’un déplacement ou d’un partage.

### 3. Créer un EPUB

Réutilisez l’instance `craft` configurée ci-dessus et remplacez la dernière ligne par :

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

Ouvrez `output.epub` dans un lecteur EPUB. Le titre, l’auteur et les options de rendu sont décrits dans [Conversion et traduction PDF](../en/PDF_TRANSLATION.md). En cas de problème, consultez le [dépannage](../en/TROUBLESHOOTING.md).

Pour obtenir les métadonnées bibliographiques d’un EPUB, activez leur extraction par OCR sur les premières pages et configurez un LLM distinct pour les métadonnées. PDF Craft ne retient que les valeurs vérifiables dans le texte OCR. Les propriétés du fichier PDF complètent les champs manquants, sans jamais remplacer les informations imprimées dans le livre.

## Traduction et réutilisation de l’extraction

**Traduire des livres.** Fournissez un `XMLTranslator` lors de la conversion PDF vers Markdown ou EPUB, ou traduisez directement un EPUB existant. La traduction utilise un LLM textuel distinct ; OCR et traduction se configurent séparément. Pour l’EPUB, vous pouvez remplacer l’original ou ajouter la traduction pour une lecture bilingue.

**Créer un PDF traduit.** Extrayez le contenu, traduisez-le et réinsérez le texte sur les pages d’origine. Ce traitement nécessite aussi des polices locales adaptées. Vérifiez la mise en page avec le texte original et sa traduction.

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

**La prise en charge des langues dépend de l’étape.** Les langues du README indiquent celles de la documentation. La reconnaissance dépend du modèle OCR ; la traduction, du traducteur et du LLM textuel. Le paramètre EPUB `label_language` choisit uniquement les libellés fixes (`zh` / `en`), avec l’anglais par défaut et en repli ; il ne limite pas les langues du contenu ou de traduction. Consultez la [référence API](../en/API_REFERENCE.md).

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

Les [Pull Requests](https://github.com/oomol-lab/pdf-craft/pulls) améliorant le code, la documentation et les traductions sont les bienvenues. Prenez le README en chinois simplifié comme référence pour le contenu. Adaptez le texte aux lecteurs de chaque langue, en mettant en avant la conversion en ligne et la traduction, tout en gardant les exemples d’API exacts. Si PDF Craft vous aide, une Star permettra à d’autres de le découvrir.

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
