<!-- Translation baseline: README.md. Keep capability descriptions and executable examples aligned. -->
<div align="center">
  <img src="../images/pdf-craft-readme-banner-v1.png" alt="PDF Craft — Превратите сканы книг в текст для чтения и редактирования." width="100%" />
  <p><a href="../../README.md">English</a> | <a href="../../README_zh-CN.md">简体中文</a> | <a href="../zh-TW/README.md">繁體中文</a> | <a href="../ja/README.md">日本語</a> | <a href="../ko/README.md">한국어</a> | <strong>Русский</strong> | <a href="../fr/README.md">Français</a> | <a href="../es/README.md">Español</a> | <a href="../de/README.md">Deutsch</a> | <a href="../it/README.md">Italiano</a></p>
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
    <a href="https://inkora.oomol.com/pdf-craft/"><strong>Попробовать онлайн</strong></a> ·
    <a href="#quick-start"><strong>Быстрый старт с Python</strong></a> ·
    <a href="#documentation"><strong>Документация</strong></a>
  </p>
</div>

Конвертируйте сканированные PDF в Markdown и EPUB, переводите содержимое и сохраняйте перевод в PDF.

## От сканов страниц к удобным документам

PDF Craft — библиотека Python для сканированных книг, научных и технических документов. Она извлекает содержимое страниц и упорядочивает текст, главы, оглавление, сноски, таблицы, формулы и изображения для чтения и редактирования.

**Markdown: редактирование, поиск и дальнейшая обработка текста.**

![Пример PDF → Markdown на английском языке](../images/pdf2md-en.png)

**EPUB: чтение в приложениях для электронных книг.**

![Пример PDF → EPUB на английском языке](../images/pdf2epub-en.png)

Результат зависит от качества скана, вёрстки и модели OCR. Проверьте типичный документ перед массовой обработкой.

## Возможности

| Задача | Решение |
| --- | --- |
| Редактировать скан книги | PDF → Markdown с текстом и файлами изображений |
| Читать в электронной читалке | PDF → EPUB с метаданными книги и оглавлением |
| Читать на другом языке | Перевод при конвертации или перевод готового EPUB; режимы только перевода и двух языков |
| Получить переведённый PDF | Перевод извлечённого текста с записью на исходные страницы |
| Встроить обработку в приложение | Python API и файлы извлечения для повторного использования |

## Выберите способ использования

| Способ | Для кого | Требования |
| --- | --- | --- |
| **[Онлайн](https://inkora.oomol.com/pdf-craft/)** | Для знакомства с результатом | Браузер; функции и условия использования указаны в онлайн-приложении |
| **Python + удалённый OCR** | Для разработчиков без локального запуска моделей | Python, Poppler, URL совместимого сервиса и учётные данные |
| **Python + локальный OCR** | Для разработчиков с NVIDIA GPU | Python, Poppler, CUDA, достаточная видеопамять и файлы моделей |

Удалённый OCR отправляет страницы настроенному сервису и не требует локальной CUDA. Локальный OCR работает на вашем компьютере; если для перевода или анализа оглавления используется удалённая LLM, соответствующее содержимое всё равно отправляется этому сервису.

**Интерфейс онлайн-приложения на английском языке**

[![PDF Craft Online](../images/website-en.png)](https://inkora.oomol.com/pdf-craft/)

<a id="quick-start"></a>

## Быстрый старт

Пример конвертирует PDF в Markdown через удалённый OCR. Подготовьте **Python 3.11–3.13, Poppler и рабочие настройки сервиса, совместимого с DeepSeek OCR**. См. [руководство по установке](../en/INSTALLATION.md). Подробные руководства по ссылкам на этой странице доступны на английском языке.

### 1. Установка

```bash
python -m pip install pdf-craft
```

### 2. Конвертация PDF

Поместите `input.pdf` в каталог, из которого запускаете скрипт. Замените URL, ключ API и название модели настройками вашего сервиса:

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

`https://example.com/v1` — адрес-заглушка. Укажите совместимый сервис, который действительно предоставляет нужную модель OCR. Другие модели описаны в [настройке OCR](../en/OCR_BACKENDS.md).

После конвертации откройте `output.md`. Если документ содержит изображения, будут созданы и файлы ресурсов. Сохраняйте их вместе с Markdown при переносе или отправке.

### 3. Создание EPUB

Используйте настроенный выше экземпляр `craft` и замените последнюю строку:

```python
craft.convert_pdf_to_epub("input.pdf", "output.epub")
```

Откройте `output.epub` в EPUB-читалке. Название книги, автор и параметры вывода описаны в [конвертации и переводе PDF](../en/PDF_TRANSLATION.md). При проблемах см. [устранение неполадок](../en/TROUBLESHOOTING.md).

## Перевод и повторное использование извлечения

**Перевод книг.** При конвертации PDF в Markdown или EPUB можно передать переводчик глав. Готовый EPUB можно перевести напрямую. Перевод использует отдельную текстовую LLM; OCR и перевод настраиваются независимо. В EPUB можно заменить оригинал переводом либо добавить перевод для двуязычного чтения.

**Переведённый PDF.** Извлеките содержимое, переведите его и запишите перевод на исходные страницы. Дополнительно нужны Ghostscript и подходящие локальные шрифты. Проверьте вёрстку с учётом оригинала и перевода.

**Извлеките один раз, используйте повторно.** Сохраните `.pcex` для дальнейшего формирования документов, перевода или обработки на другом компьютере. Используйте настроенный экземпляр `craft`:

```python
craft.convert_pdf_to_markdown(
    "input.pdf",
    "output.md",
    extraction_path="book.pcex",
)
```

См. [конвертацию и перевод PDF](../en/PDF_TRANSLATION.md), [перевод EPUB](../en/EPUB_TRANSLATION.md) и [формат `.pcex`](../en/PCEX_FORMAT.md).

## OCR и требования к среде

Поддерживаются **DeepSeek OCR, DeepSeek OCR 2 и Unlimited OCR**, каждый с локальной и удалённой конфигурацией. Для удалённого OCR достаточно стандартной установки. Для локального OCR установите дополнительные зависимости:

```bash
python -m pip install "pdf-craft[local]"
```

Также нужны совместимая сборка PyTorch с CUDA, достаточная видеопамять и файлы моделей. По умолчанию модели загружаются с Hugging Face; их можно скачать заранее и читать локально. Пресеты и требования различаются: см. [настройку OCR](../en/OCR_BACKENDS.md).

**Поддержка языков зависит от этапа обработки.** Языки README обозначают доступность документации. Распознавание зависит от модели OCR, перевод — от переводчика и текстовой LLM. Параметр EPUB `lan` сейчас предлагает `zh` / `en`; см. [справочник API](../en/API_REFERENCE.md).

<a id="documentation"></a>

## Документация

Подробные руководства ниже — на английском языке.

| Задача | Руководство |
| --- | --- |
| Системные зависимости и локальный GPU | [Установка](../en/INSTALLATION.md) |
| Модели, удалённые сервисы и кэш | [Настройка OCR](../en/OCR_BACKENDS.md) |
| Конвертация PDF, создание EPUB и перевод PDF | [Конвертация и перевод PDF](../en/PDF_TRANSLATION.md) |
| Перевод готового EPUB и двуязычный вывод | [Перевод EPUB](../en/EPUB_TRANSLATION.md) |
| Параметры, типы и методы | [Справочник API](../en/API_REFERENCE.md) |
| Хранение и обмен извлечёнными данными | [Формат `.pcex`](../en/PCEX_FORMAT.md) |
| Проблемы установки и конвертации | [Устранение неполадок](../en/TROUBLESHOOTING.md) |

## Обратная связь и участие

Сообщайте об ошибках и предложениях через [Issues](https://github.com/oomol-lab/pdf-craft/issues). Для проблем конвертации приложите версию пакета, тип конфигурации OCR, журнал ошибок и минимальный файл, который можно опубликовать. Предварительно удалите ключи и личные данные.

Приветствуются [Pull Requests](https://github.com/oomol-lab/pdf-craft/pulls) с улучшениями кода, документации и переводов. Описания возможностей и примеры в переводах README должны соответствовать английской версии. Если проект полезен, поставьте Star, чтобы о нём узнали другие.

## Связанные проекты

[Wiki Graph](https://github.com/oomol-lab/wiki-graph) создаёт структурированные краткие изложения, топологию глав и графы знаний из книг в EPUB или Markdown.

## Лицензия и благодарности

PDF Craft распространяется по [лицензии MIT](../../LICENSE). Сторонние зависимости и выбранные модели OCR имеют собственные лицензии.

Благодарим [DeepSeek OCR](https://github.com/deepseek-ai/DeepSeek-OCR), [DeepSeek OCR 2](https://github.com/deepseek-ai/DeepSeek-OCR-2), [Unlimited OCR](https://github.com/baidu/Unlimited-OCR), [doc-page-extractor](https://github.com/Moskize91/doc-page-extractor), [pyahocorasick](https://github.com/WojciechMula/pyahocorasick) и другие проекты с открытым исходным кодом.

<!-- community-footer:start -->

## Участники

Спасибо всем, кто внёс вклад в PDF Craft. Мы рады улучшениям кода, документации и переводов.

[![Участники PDF Craft](https://contrib.rocks/image?repo=oomol-lab/pdf-craft)](https://github.com/oomol-lab/pdf-craft/graphs/contributors)

## Star History

<!-- star-history:start -->
<a href="https://www.star-history.com/#oomol-lab/pdf-craft&amp;Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date&amp;theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
    <img alt="История звёзд PDF Craft" src="https://api.star-history.com/svg?repos=oomol-lab/pdf-craft&amp;type=Date" />
  </picture>
</a>
<!-- star-history:end -->
<!-- community-footer:end -->
