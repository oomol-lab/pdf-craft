<!-- Translation baseline: README.md. Keep capability descriptions and executable examples aligned. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — Convierte tus libros escaneados en texto que puedas editar y leer." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="../../README_zh-CN.md">简体中文</a> | <a href="../zh-TW/README.md">繁體中文</a> | <a href="../ja/README.md">日本語</a> | <a href="../ko/README.md">한국어</a> | <a href="../ru/README.md">Русский</a> | <a href="../fr/README.md">Français</a> | <strong>Español</strong> | <a href="../de/README.md">Deutsch</a> | <a href="../it/README.md">Italiano</a></p>
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
    <a href="https://inkora.oomol.com/pdf-craft/"><strong>Probar en línea</strong></a> ·
    <a href="#quick-start"><strong>Inicio rápido con Python</strong></a> ·
    <a href="#documentation"><strong>Documentación</strong></a>
  </p>
</div>

Convierte PDF escaneados a Markdown y EPUB, traduce su contenido y guarda el texto traducido en un PDF.

## De páginas escaneadas a documentos útiles

PDF Craft es una biblioteca de Python para libros escaneados y documentos académicos o técnicos. Extrae el contenido de las páginas y organiza el texto, los capítulos, el índice, las notas al pie, las tablas, las fórmulas y las imágenes para facilitar su edición y lectura.

**Markdown: para editar, buscar y procesar el contenido.**

![Ejemplo de PDF a Markdown en inglés](../images/pdf2md-en.png)

**EPUB: para leer en un lector de libros electrónicos.**

![Ejemplo de PDF a EPUB en inglés](../images/pdf2epub-en.png)

El resultado depende de la calidad del escaneo, la maquetación y el modelo OCR. Comprueba un documento representativo antes de procesar una colección completa.

## Qué puedes hacer

| Objetivo | Función |
| --- | --- |
| Editar libros escaneados | PDF → Markdown, con texto y archivos de imágenes |
| Leer en un lector electrónico | PDF → EPUB, con metadatos del libro e índice |
| Leer en otro idioma | Traducción durante la conversión o de un EPUB existente; salida solo traducida o bilingüe |
| Crear un PDF traducido | Traducir el texto extraído y escribirlo en las páginas originales |
| Integrar la conversión en una aplicación | API de Python y archivos de extracción reutilizables |

## Elige cómo usarlo

| Opción | Para quién | Requisitos |
| --- | --- | --- |
| **[En línea](https://inkora.oomol.com/pdf-craft/)** | Quienes quieran probar el resultado | Navegador; funciones y condiciones indicadas en la aplicación en línea |
| **Python + OCR remoto** | Desarrolladores que no ejecutan modelos localmente | Python, Poppler, URL y credenciales de un servicio compatible |
| **Python + OCR local** | Desarrolladores con GPU NVIDIA | Python, Poppler, CUDA, suficiente VRAM y archivos de modelos |

El OCR remoto envía las páginas al servicio configurado y no necesita CUDA local. El OCR local se ejecuta en tu equipo; si usas un LLM remoto para traducir o analizar el índice, el contenido correspondiente sí se envía a ese servicio.

<details>
<summary>Vista de la aplicación en línea, en inglés</summary>

[![PDF Craft en línea](../images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

</details>

<a id="quick-start"></a>

## Inicio rápido

Este ejemplo convierte un PDF a Markdown con OCR remoto. Prepara **Python 3.11–3.13, Poppler y una configuración válida de un servicio compatible con DeepSeek OCR**. Consulta la [guía de instalación](../en/INSTALLATION.md). Las guías detalladas enlazadas desde esta página están en inglés.

### 1. Instala

```bash
python -m pip install pdf-craft
```

### 2. Convierte un PDF

Coloca `input.pdf` en el directorio desde el que ejecutas el script. Sustituye la URL, la clave API y el nombre del modelo por los de tu servicio:

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

`https://example.com/v1` es una dirección de ejemplo, no un servicio operativo. Usa un servicio compatible que proporcione el modelo OCR. Consulta otros modelos en la [configuración de OCR](../en/OCR_BACKENDS.md).

Al terminar, abre `output.md`. Los documentos con imágenes también generan archivos de recursos; consérvalos junto al Markdown al moverlo o compartirlo.

### 3. Genera un EPUB

Reutiliza la instancia `craft` ya configurada y sustituye la última línea por:

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

Abre `output.epub` en un lector EPUB. Para configurar título, autor y salida, consulta [Conversión y traducción de PDF](../en/PDF_TRANSLATION.md). Si hay problemas de instalación o ejecución, consulta [Solución de problemas](../en/TROUBLESHOOTING.md).

## Traducción y reutilización de la extracción

**Traduce libros.** Puedes proporcionar un traductor de capítulos al convertir PDF a Markdown o EPUB, o traducir directamente un EPUB existente. La traducción usa un LLM de texto independiente; OCR y traducción se configuran por separado. En EPUB puedes sustituir el original o añadir la traducción para una lectura bilingüe.

**Crea un PDF traducido.** Extrae y traduce el contenido, y escribe el texto traducido en las páginas originales. También necesitas Ghostscript y fuentes locales adecuadas. Revisa la maquetación según el original y la traducción.

**Extrae una vez y reutiliza después.** Guarda un archivo `.pcex` para generar otros documentos, traducir o procesar en otro equipo. Reutiliza la instancia `craft` configurada:

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

Consulta [Conversión y traducción de PDF](../en/PDF_TRANSLATION.md), [Traducción de EPUB](../en/EPUB_TRANSLATION.md) y el [formato `.pcex`](../en/PCEX_FORMAT.md).

## OCR y requisitos de ejecución

PDF Craft admite **DeepSeek OCR, DeepSeek OCR 2 y Unlimited OCR**, cada uno con configuración local y remota. La instalación estándar permite usar OCR remoto. Para OCR local, instala las dependencias adicionales:

```bash
python -m pip install "pdf-craft[local]"
```

También necesitas una versión compatible de PyTorch con CUDA, suficiente VRAM y los archivos de modelos. Por defecto se descargan de Hugging Face; puedes descargarlos de antemano y cargarlos localmente. Los ajustes predefinidos y requisitos varían: consulta la [configuración de OCR](../en/OCR_BACKENDS.md).

**Los idiomas disponibles dependen de la etapa.** Los idiomas del README indican los de la documentación. El reconocimiento depende del modelo OCR, y la traducción depende del traductor y del LLM de texto. El parámetro EPUB `lan` ofrece actualmente `zh` / `en`; consulta la [referencia de la API](../en/API_REFERENCE.md).

<a id="documentation"></a>

## Documentación

Las siguientes guías detalladas están en inglés.

| Necesidad | Guía |
| --- | --- |
| Dependencias del sistema y GPU local | [Instalación](../en/INSTALLATION.md) |
| Modelos, servicios remotos y caché | [Configuración de OCR](../en/OCR_BACKENDS.md) |
| Conversión PDF, creación EPUB y PDF traducido | [Conversión y traducción de PDF](../en/PDF_TRANSLATION.md) |
| EPUB existentes y salida bilingüe | [Traducción de EPUB](../en/EPUB_TRANSLATION.md) |
| Parámetros, tipos y métodos | [Referencia de la API](../en/API_REFERENCE.md) |
| Guardar e intercambiar extracciones | [Formato `.pcex`](../en/PCEX_FORMAT.md) |
| Problemas de instalación y conversión | [Solución de problemas](../en/TROUBLESHOOTING.md) |

## Comentarios y contribuciones

Comunica problemas o sugerencias en [Issues](https://github.com/oomol-lab/pdf-craft/issues). Para problemas de conversión, incluye la versión del paquete, el tipo de configuración OCR, los registros de error y un archivo mínimo que puedas compartir públicamente. Elimina las credenciales y el contenido privado.

Son bienvenidas las [Pull Requests](https://github.com/oomol-lab/pdf-craft/pulls) que mejoren el código, la documentación y las traducciones. Mantén las funciones y los ejemplos de los README traducidos alineados con la versión inglesa. Si PDF Craft te resulta útil, dale una Star para que otras personas lo descubran.

## Proyectos relacionados

[Wiki Graph](https://github.com/oomol-lab/wiki-graph) transforma libros EPUB o Markdown en resúmenes estructurados, topologías de capítulos y grafos de conocimiento.

## Licencia y agradecimientos

PDF Craft utiliza la [licencia MIT](../../LICENSE). Las dependencias de terceros y los modelos OCR seleccionados conservan sus propias licencias.

Gracias a [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) y a los demás proyectos de código abierto.

<!-- community-footer:start -->

## Colaboradores

Gracias a todas las personas que han contribuido a PDF Craft. Son bienvenidas las mejoras del código, la documentación y las traducciones.

[![Colaboradores de PDF Craft](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="Historial de estrellas de PDF Craft" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
