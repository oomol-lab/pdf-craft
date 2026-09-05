# PDFCraftExtraction（`.pcex`）格式参考

本文是 PDFCraftExtraction v1 的中文版格式参考。它描述当前 pdf-craft 代码能够生成、读取和校验的公开中间格式，以及各成员被后续渲染、翻译和 PDF 写回流程使用的方式。

本文中的“规范产物”指 pdf-craft 自身写出的 `.pcex`；“当前校验器”指 `PDFCraftExtraction.open()` 或 `PDFCraftExtraction.validate()` 所执行的校验。两者需要区分：规范产物会遵循本文给出的字段关系，但当前校验器并未检查其中每一项语义关系。

## 格式定位

PDFCraftExtraction 是 pdf-craft 从 PDF 提取出的结构化文档。它处在 OCR/PDF 前端和 Markdown、EPUB、PDF 写回等后端之间，保存：

- 文档元数据和格式版本；
- 原 PDF 的页码、页面像素尺寸和 OCR 坐标空间；
- 按章节组织的正文、标题、公式、表格、图片和脚注；
- 文本块、资源与原 PDF 页面的 bbox 映射；
- 可选目录和封面。

`.pcex` 不包含原 PDF，也不包含 OCR 模型响应、逐页 OCR 缓存、失败标记或诊断图。因此它可以单独复制、上传和跨机器传递，用于无需再次 OCR 的后续处理；若要把内容写回 PDF，调用方仍须另行保存原 PDF。

公开交换形态始终是扩展名为 `.pcex` 的 ZIP 归档。解压后的目录只是格式的物理内容，不是受支持的公开输入形式。

## 快速索引

一个规范归档具有以下结构：

```text
book.pcex                       # ZIP（Deflate 压缩）
├── manifest.json               # 必需：格式、生产者和文档元数据
├── pages.xml                   # 必需：页面几何和坐标空间
├── chapters/                   # 必需，可为空
│   ├── chapter_head.xml        # 可选：第一个正式章节前的内容
│   └── chapter_<id>.xml        # 零个或多个正式章节
├── assets/                     # 必需，可为空
│   └── <sha256>.png            # 零个或多个内容资源
├── toc.xml                     # 可选：层级目录
└── cover.png                   # 可选：封面
```

根目录和两个子目录不允许出现上表之外的成员。成员名称区分大小写；公开文件路径的 `.pcex` 后缀检查不区分大小写。

| 成员 | 必需 | 内容 | 主要消费者 |
| --- | --- | --- | --- |
| `manifest.json` | 是 | 版本、生产者、时间、书目元数据和语言 | 加载器、EPUB 渲染器 |
| `pages.xml` | 是 | DPI、页面像素尺寸、坐标系 | 校验器、PDF 写回 |
| `chapters/` | 是 | 结构化章节和原 PDF 位置 | Markdown/EPUB 渲染、翻译、PDF 写回 |
| `assets/` | 是 | 以内容哈希命名的 PNG | Markdown/EPUB 渲染 |
| `toc.xml` | 否 | 目录树及目录页 | EPUB 渲染、章节关系 |
| `cover.png` | 否 | 封面图 | Markdown/EPUB 渲染 |

pdf-craft 写出的 JSON 和 XML 文本均使用 UTF-8；XML 文件带有 `<?xml version="1.0" encoding="UTF-8"?>` 声明。ZIP 内路径统一使用 `/`。

v1 没有 `document.json` 或 `source-map.json`。文档元数据集中在 `manifest.json`，页面几何集中在 `pages.xml`，每个内容块到原 PDF 的位置映射直接保存在章节 XML 中。

## 获取、保存和继续处理

### 从 PDF 生成

`PDFCraft.extract_pdf()` 是常规公开入口。第二个参数既决定归档的存储位置，也必须以 `.pcex` 结尾：

```python
from pdf_craft import PDFCraft, PDFOptions

craft = PDFCraft(pdf=PDFOptions(...))
extraction = craft.extract_pdf("book.pdf", "output/book.pcex")
```

成功后，归档位于调用方给出的 `output/book.pcex`，返回值是可立即继续使用的 `PDFCraftExtraction`。目标文件已存在时不会覆盖，而是抛出 `FileExistsError`。父目录不存在时会自动创建。

需要 OCR token 计量时：

```python
extraction, metering = craft.extract_pdf_with_metering(
    "book.pdf", "output/book.pcex"
)
```

一键转换也能顺便保留中间产物：

```python
craft.convert_pdf_to_markdown(
    "book.pdf",
    "output/book.md",
    extraction_path="output/book.pcex",
)
```

`convert_pdf_to_markdown()` 和 `convert_pdf_to_epub()` 在一次完整转换中使用内部未压缩工作区直接衔接前后端；只有提供 `extraction_path` 时才额外写出 `.pcex`，不会为了内部传递而先压缩再解压。

### 打开和校验

```python
from pdf_craft import PDFCraftExtraction

extraction = PDFCraftExtraction.open("output/book.pcex")
extraction.validate()
```

以下三种写法等价，都会在构造期间打开、解压并校验归档：

```python
PDFCraftExtraction("output/book.pcex")
PDFCraftExtraction.open("output/book.pcex")
PDFCraftExtraction.load("output/book.pcex")
```

路径不是普通文件时抛出 `FileNotFoundError`；后缀不是 `.pcex` 时抛出 `ValueError`。目录即使包含完整成员也不能作为公开输入。

`validate()` 校验成功时返回对象自身，便于链式使用。`validate(require_toc=True)` 还会要求 `toc.xml` 存在；EPUB 渲染使用这一模式，Markdown 渲染只要求基本格式有效。

### 跨机器继续后端

机器 A 完成 OCR：

```python
craft = PDFCraft(pdf=PDFOptions(...))
craft.extract_pdf("book.pdf", "book.pcex")
```

将单个 `book.pcex` 传到机器 B 后，可以不配置 PDF/OCR 基础设施：

```python
from pdf_craft import PDFCraft, PDFCraftExtraction

extraction = PDFCraftExtraction.open("book.pcex")
craft = PDFCraft()
craft.render_markdown(extraction, "book.md")
craft.render_epub(extraction, "book.epub")  # 要求包内有 toc.xml
```

这些后端只读取 extraction 内部成员，不会查找原机器的 analysis 目录或 `ocr/` 缓存。

### 翻译、导出和渲染

所有接受 extraction 的 `PDFCraft` 后端都可接收 `PDFCraftExtraction` 对象，也可直接接收 `.pcex` 路径：

```python
craft.render_markdown("book.pcex", "book.md", assets_path="book-assets")
craft.render_epub("book.pcex", "book.epub")

translated = craft.translate_extraction(
    "book.pcex", "book.zh.pcex", translator
)
```

`translate_extraction()` 创建新的 `.pcex`，保留原包的 manifest、页面几何、目录、封面和资源，只重写经过 transformer 处理的章节 XML。输出路径必须以 `.pcex` 结尾且不能已存在。

`PDFCraftExtraction.export(path)` 会把当前对象重新校验并写成新的 `.pcex`，返回由新归档支撑的对象。写入使用同目标目录中的临时文件，成功后原子替换为目标名称；现有目标仍不会被覆盖。ZIP 成员的时间戳等容器元数据不属于稳定格式，不能假设两次导出逐字节相同。

### 公开元数据读取方法

`PDFCraftExtraction` 不公开内部解压目录，也不把章节目录暴露为公共编辑接口。它提供以下只读方法：

| 方法 | 返回值 |
| --- | --- |
| `page_pixel_sizes()` | `{页码: (像素宽度, 像素高度)}` 的新字典 |
| `render_dpi()` | `pages.xml` 中的正整数 DPI |
| `document_metadata()` | `manifest.json` 中 `document` 对象的浅拷贝 |
| `language()` | `document.language` 的字符串或 `None` |
| `book_meta()` | 由文档元数据构造的 `epub_generator.BookMeta` |

## `manifest.json`

### 完整示例

```json
{
  "format_version": 1,
  "producer": {
    "name": "pdf-craft",
    "version": "2.0.0"
  },
  "created_at": "2026-09-05T03:20:00.000000+00:00",
  "document": {
    "title": "示例书",
    "description": null,
    "publisher": "示例出版社",
    "isbn": null,
    "authors": ["作者甲"],
    "editors": [],
    "translators": [],
    "modified": "2026-08-20T12:00:00+08:00",
    "language": "zh"
  }
}
```

顶层必须是 JSON 对象，不允许出现未列出的顶层字段。

| 字段 | 类型 | 必需 | 含义与约束 |
| --- | --- | --- | --- |
| `format_version` | integer | 是 | 当前唯一支持的值为 `1` |
| `producer` | object | 是 | 创建归档的软件标识；必须且只能含 `name`、`version` |
| `created_at` | string 或 null | 否 | 归档创建时间；字符串须为可解析的 ISO 8601 时间 |
| `document` | object | 是 | 文档级元数据；必须且只能含下一节的九个字段 |

规范值 `format_version` 是 JSON 数字 `1`。当前实现直接把 JSON 解码值与 Python 整数 `1` 比较，而没有额外执行 JSON 类型断言；生产者不应利用布尔值与整数相等之类的语言细节。

pdf-craft 自身写出时，`producer.name` 固定为 `pdf-craft`，`producer.version` 是已安装的 pdf-craft 包版本；无法取得安装版本时为 `unknown`。当前校验器允许其他生产者，但 `name` 和 `version` 都必须是非空字符串。

pdf-craft 自身总会写出 `created_at`，使用带 UTC 时区偏移的当前时间。当前读取器也接受缺失或为 `null` 的 `created_at`；若为字符串，则必须能被 Python `datetime.fromisoformat()` 解析。格式不要求该时间一定为 UTC。

### `document` 对象

九个字段全部必须存在；没有值的单值字段使用 `null`，没有成员的贡献者字段使用空数组。不能省略字段，也不能增加字段。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `title` | string 或 null | 书名；PDF 元数据可读取但标题缺失时，pdf-craft 使用源文件名（不含扩展名） |
| `description` | string 或 null | 文档描述 |
| `publisher` | string 或 null | 出版者 |
| `isbn` | string 或 null | ISBN；格式不进一步规定字符形态 |
| `authors` | string[] | 作者，保持数组顺序 |
| `editors` | string[] | 编辑，保持数组顺序 |
| `translators` | string[] | 译者，保持数组顺序 |
| `modified` | string 或 null | 文档修改时间；字符串须为 ISO 8601 时间 |
| `language` | string 或 null | 文档语言标识 |

使用默认 PDF 读取器提取时，`modified` 并不在 `/ModDate` 缺失时写成 `null`：读取器先以“读取元数据时的当前 UTC 时间”作为默认值；只有 `/ModDate` 存在且其年月日时分秒可成功解析时，才用解析结果替换默认值。`/ModDate` 缺失、为空、长度不足或日期解析失败时，manifest 因而保留当前 UTC 时间。当前解析器取 PDF 日期的前 14 位年月日时分秒并标记为 UTC，不解释其后可能存在的 PDF 时区偏移。只有元数据读取整体抛出 `PDFError`、提取器无法取得任何 `BookMeta` 时，`modified` 才会随空元数据一起写为 `null`。

`language` 当前不限制为特定语言代码，但 EPUB 渲染器只支持 `zh` 和 `en`。渲染 EPUB 时，调用参数 `lan` 优先，其次是此字段，最后默认为 `zh`。调用时显式传入的 `book_meta` 同样优先于 manifest 中转换得到的 `BookMeta`。

从普通 PDF 提取时，pdf-craft 写入 PDF 书目元数据，但当前提取流程没有自动判定语言，因此 `language` 通常为 `null`。翻译 extraction 时不会自动改写 manifest 或语言。

## `pages.xml`

### 完整示例

```xml
<?xml version="1.0" encoding="UTF-8"?>
<pages index_base="1" coordinate_space="ocr_pixels" render_dpi="300">
  <page index="1" width="2480" height="3508" />
  <page index="2" width="2480" height="3508" />
  <page index="3" width="2480" height="3508" />
</pages>
```

根元素必须是 `<pages>`，并且必须且只能带三个属性：

| 属性 | 固定值/类型 | 含义 |
| --- | --- | --- |
| `index_base` | `1` | 包内所有原 PDF 页码从 1 开始 |
| `coordinate_space` | `ocr_pixels` | bbox 使用 OCR 页面位图的像素坐标 |
| `render_dpi` | 正整数 | 提取时请求的页面渲染 DPI；未显式指定时为 `300` |

每个直接子元素必须是 `<page>`，且必须且只能包含：

| 属性 | 类型 | 含义与约束 |
| --- | --- | --- |
| `index` | 正整数 | 原 PDF 页码；在文件内唯一 |
| `width` | 正整数 | 该页 OCR 位图的像素宽度 |
| `height` | 正整数 | 该页 OCR 位图的像素高度 |

`<page>` 不能包含子元素，规范写法也不包含文本。当前校验器不读取 page 的文本节点。页元素可以为空或稀疏，例如只提取指定页时只记录实际得到几何信息的页；所有章节元素引用的页则必须存在。页元素的物理排列不改变页码含义，pdf-craft 写出时按 `index` 升序排列。

### 坐标和 bbox

章节中的 `det` 属性统一写成：

```text
left,top,right,bottom
```

四项都是十进制整数，原点位于 OCR 页面位图左上角，横轴向右，纵轴向下；边界按 PIL 裁剪框语义表示左上角和右下角。必须满足：

```text
0 <= left < right <= page.width
0 <= top  < bottom <= page.height
```

因此 `pages.xml` 是所有下游解释页码和 bbox 的唯一 extraction 内数据源。analysis 中可能仍有 OCR 页面尺寸缓存，但后端不会回退读取它。

## `toc.xml`

### 完整示例

```xml
<?xml version="1.0" encoding="UTF-8"?>
<toc page_indexes="2">
  <item id="1" page_index="3" order="0" level="0">
    <item id="2" page_index="8" order="1" level="1" />
  </item>
</toc>
```

`toc.xml` 整体可选，但 EPUB 渲染要求它存在；空目录写作 `<toc page_indexes="" />`。

根元素必须为 `<toc>`。必需属性 `page_indexes` 是以英文逗号连接的原 PDF 页码，表示识别为印刷目录、因而没有进入章节正文的页面。没有目录页时值为空字符串。

根元素和 `<item>` 可以包含任意数量的直接 `<item>`，嵌套关系就是目录层级。每个 `<item>` 必须包含以下可解析为整数的属性：

| 属性 | 含义 |
| --- | --- |
| `id` | 目录项 ID；规范产物从 `1` 起分配，并对应章节根元素的 `id` 与 `chapter_<id>.xml` |
| `page_index` | 该标题在原 PDF 中的 1-based 页码 |
| `order` | 标题在该页 OCR 布局中的 0-based 顺序 |
| `level` | 0-based 全局目录级别；`0` 是最高级 |

`page_index` 和 `order` 共同指向生成该目录项的标题布局。`level` 同时用于计算 Markdown/EPUB 标题层级；XML 的嵌套结构则表达父子关系。

当前 v1 校验器会检查根元素、`page_indexes` 的整数列表、所有子元素名称，以及每项四个必需整数属性；它暂不检查页码是否存在于 `pages.xml`、ID 是否唯一、`level` 是否与嵌套深度一致，也不检查 ID 是否确实对应章节。格式生产者仍应保持上述关系。

## `chapters/`

### 文件命名和读取顺序

目录内只允许两类普通 XML 文件：

- `chapter_head.xml`：可选，保存第一个正式目录章节之前的正文；
- `chapter_<十进制数字>.xml`：正式章节，数字通常等于章节/目录项 ID。

不允许子目录、符号链接或其他文件。章节目录可以为空。读取时 `chapter_head.xml` 最先，其余章节按文件名数字部分的整数值升序读取，而不是按 ZIP 成员顺序或字典序读取。

规范生产者以章节 `id` 直接生成无前导零的文件名；当前校验器只检查文件名形态，没有检查文件名数字、章节 `id` 和 TOC `id` 三者相等，也没有拒绝映射到相同整数的不同写法。

### 章节完整示例

下面的示例同时覆盖正文、行内公式、图片和脚注引用：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<chapter id="1" level="0">
  <body>
    <paragraph ref="title" level="0">
      <block page_index="3" order="0" det="180,210,2260,360">第一章</block>
    </paragraph>
    <paragraph ref="text">
      <block page_index="3" order="1" det="180,410,2260,620">能量为 <inline_expr kind="$">E=mc^2</inline_expr>。<ref id="3-1" /></block>
    </paragraph>
    <asset ref="image" page_index="3" det="400,700,2080,1800" hash="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">
      <caption>图 1：示意图</caption>
    </asset>
  </body>
  <references>
    <ref id="3-1">
      <mark>①</mark>
      <paragraph ref="text">
        <block page_index="3" order="5" det="180,3200,2260,3370">脚注正文。</block>
      </paragraph>
    </ref>
  </references>
</chapter>
```

示例中的图片要求同时存在：

```text
assets/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png
```

### `<chapter>`

根元素必须是 `<chapter>`。

| 属性 | 必需 | 类型与含义 |
| --- | --- | --- |
| `id` | 否 | 整数目录项 ID；省略表示 head 章节 |
| `level` | 否 | 整数章节层级；省略时内部值为 `-1`，规范正式章节通常取 TOC 的 0-based level |

每章必须有一个可找到的 `<body>`。规范顺序为 `<body>` 后跟可选 `<references>`。`<body>` 的直接子元素按文档顺序排列，可以是 `<paragraph>` 或 `<asset>`。

### `<paragraph>`

```xml
<paragraph ref="text" level="1">
  <block page_index="4" order="2" det="180,500,2260,760">内容</block>
  <block page_index="5" order="0" det="180,200,2260,430">跨页续文</block>
</paragraph>
```

| 属性 | 必需 | 含义 |
| --- | --- | --- |
| `ref` | 是 | OCR 布局种类字符串 |
| `level` | 否 | 0-based 章内标题层级；省略时为 `-1` |

`ref="title"` 和 `ref="sub_title"` 都会被后端作为标题处理；`ref="text"` 作为普通正文。格式读取器会保留其他 `ref` 字符串，渲染器通常将它们按普通段落处理。

标题段落的 `level="0"` 表示章节主标题，更大的值表示章内更深的标题。非标题段落通常省略 `level`。Markdown 最终标题级别还会叠加章节的 `level`，并限制在六级以内。

一个段落由零个或多个 `<block>` 组成。跨页或跨 OCR 布局合并的段落会含多个 block，因此位置映射属于 block 而不是 paragraph。

### `<block>`

| 属性 | 必需 | 类型与含义 |
| --- | --- | --- |
| `page_index` | 是 | 正整数原 PDF 页码，必须存在于 `pages.xml` |
| `order` | 是 | 整数；该 block 在页面 OCR 布局中的 0-based 顺序 |
| `det` | 是 | `left,top,right,bottom`，必须落在对应页面范围内 |

block 是文本与原 PDF 位置之间的最小映射单位。其内容使用 XML mixed content：普通文字可以出现在 `.text` 或子元素 `.tail` 中，还可以嵌入下文定义的行内公式、脚注引用和 HTML 包装元素。元素顺序就是内容顺序。

PDF 写回只处理 `ref` 为 `text` 或 `sub_title` 的段落 block；它使用 `page_index`、`det`、`order` 以及 `pages.xml` 的页面几何来放置文字。其他渲染器仍会消费全部段落内容。

### `<asset>`

```xml
<asset
  ref="table"
  page_index="6"
  det="220,600,2200,1700"
  hash="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
>
  <title>表 1</title>
  <content><table><tr><td>A</td></tr></table></content>
  <caption>数据来源</caption>
</asset>
```

| 属性 | 必需 | 含义 |
| --- | --- | --- |
| `ref` | 是 | 只能是 `image`、`table` 或 `equation` |
| `page_index` | 是 | 正整数原 PDF 页码，必须存在于 `pages.xml` |
| `det` | 是 | 资源在该页的 bbox，必须落在页面范围内 |
| `hash` | 否 | 对应 `assets/<hash>.png` 的 64 位小写十六进制名称 |

`<asset>` 可按此顺序包含零个或一个 `<title>`、`<content>`、`<caption>`。三者均使用与 block 相同的 mixed content 和 HTML 包装元素，但不允许包含脚注 `<ref>`；可以包含 `<inline_expr>`。

不同 `ref` 的后端含义：

| `ref` | `content` | `hash` 与 PNG 的用途 |
| --- | --- | --- |
| `image` | 通常为空 | 图片本体；没有 hash 时渲染器忽略图片本体 |
| `table` | HTML 表格，若 OCR 能恢复结构 | 表格截图；EPUB 无可用 HTML 表格时用它回退为图片 |
| `equation` | LaTeX 公式正文 | 可选的公式区域截图；常规 Markdown/EPUB 公式渲染使用正文 |

只要写出了 `hash`，当前校验器就要求相应 PNG 存在，无论 asset 是哪一种。`title` 和 `caption` 是资源前后的可渲染说明文字。Markdown 可以直接渲染有结构化 `content` 而无 hash 的表格；当前 EPUB 渲染器则会先要求表格带 hash，即使最终采用的是 HTML 表格内容，没有 hash 的表格会被忽略。

### 行内公式 `<inline_expr>`

`<inline_expr>` 可出现在 block 以及 asset 的 title/content/caption 中：

```xml
<inline_expr kind="\(">x^2+y^2</inline_expr>
```

`kind` 必需，值只能为：

| `kind` | 语义/恢复出的 Markdown 定界符 |
| --- | --- |
| `text` | 被表达式节点承载的普通文本 |
| `$` | `$ ... $` 行内公式 |
| `$$` | `$$ ... $$` 展示公式 |
| `\(` | `\( ... \)` 行内公式 |
| `\[` | `\[ ... \]` 展示公式 |

元素文本是定界符内部内容，不包含两端定界符。未知 `kind` 会使章节解码失败。

### HTML 包装元素

文本 mixed content 可以嵌套以下 HTML 元素，用来保留 OCR 结果中的 Markdown/HTML 结构：

```text
div p blockquote details summary figure figcaption
h1 h2 h3 h4 h5 h6
b i strong em small mark s strike abbr cite dfn kbd samp var code pre tt
q bdo ins del sup sub span
ol ul li dl dt dd
table thead tbody tfoot tr td th caption
img picture source video
a br hr time wbr ruby rt rp
```

元素可继续包含普通文字、其他允许的 HTML 包装元素和所在上下文允许的 payload（`inline_expr`，以及 block 中的 `ref`）。HTML 元素名匹配不区分大小写，解码后使用规范的小写名称。

pdf-craft 从 OCR Markdown 生成这些节点时，会按 GFM 风格白名单过滤属性和 URL 协议；事件处理属性等不会由规范生产者写出。归档读取器目前只依据元素名识别 HTML 包装，不会再次过滤其属性：已知 HTML 元素上的 XML 属性会被保留并由渲染器输出。因此，不应把打开第三方 `.pcex` 等同于 HTML 安全净化，展示输出时仍应遵循宿主环境的安全策略。

### 脚注和引用

正文 block 中的：

```xml
<ref id="3-1" />
```

指向同章 `<references>` 内 ID 相同的引用定义：

```xml
<references>
  <ref id="3-1">
    <mark>①</mark>
    <paragraph ref="text">...</paragraph>
    <asset ref="image" ...>...</asset>
  </ref>
</references>
```

ID 格式严格为 `<page_index>-<order>`，两段都必须是整数。这里的 `order` 是 pdf-craft 在该页抽取脚注时从 `1` 起分配的引用序号，不是正文 block 的 0-based OCR 布局顺序。

每个引用定义必须包含带文本的 `<mark>`，保存正文中原本出现的脚注标记，例如 `①` 或 `*`。其后可以包含任意数量的 paragraph 或 asset，表示脚注正文。引用正文中的 paragraph block 仍保存自己的页码、OCR 顺序和 bbox。

正文中的每个 `<ref>` 必须能在同章解析到定义，否则章节无效。引用定义内部不能再嵌套另一个脚注 `<ref>`。规范编码器只输出正文实际引用到的定义，按 `(page_index, order)` 排序，并在 Markdown/EPUB 中将它们统一重新编号。

当前解码器以 ID 建立映射但没有单独拒绝重复定义；格式生产者应保证同章引用 ID 唯一。定义 ID 自身包含的页码也暂不与 `pages.xml` 交叉校验，但其子布局的 `page_index` 和 bbox 会正常校验。

## `assets/`

资源文件名必须严格匹配：

```text
[0-9a-f]{64}.png
```

规范生产者先把裁剪区域编码为 PNG，再以完整文件字节的 SHA-256 小写十六进制摘要命名。相同字节会复用同一文件，因此多个 asset 可以引用同一 hash。

目录内只允许普通文件，不允许子目录、符号链接或其他扩展名。当前校验器会检查文件名形态，并检查每个章节 `<asset hash="...">` 指向的文件存在；它不会重新计算文件内容的 SHA-256，也不会验证文件确实可解码为 PNG。未被章节引用但名称合法的孤立资源目前也允许存在。

## `cover.png`

封面是可选根成员。启用 `ExtractionOptions(includes_cover=True)` 时，pdf-craft 尝试保存第一页 OCR 过程中取得的原始页面图；如果未得到可用图像，则不创建该文件。

Markdown 渲染会把封面复制到输出资源目录，但不会自动在 Markdown 正文插入封面语法；EPUB 渲染把它作为 EPUB 封面。当前校验器只要求该成员是普通文件，不校验 PNG 内容。

## 跨文件不变量

规范 `.pcex` 应满足以下关系：

| 来源 | 目标/约束 |
| --- | --- |
| chapter 中任意带 `page_index` 的元素 | 页码必须存在于 `pages.xml` |
| 同一元素上的 `det` | 必须落在对应 `<page>` 的宽高内 |
| `<asset hash="H">` | `assets/H.png` 必须存在，且 H 为 64 位小写十六进制 |
| block 中 `<ref id="P-O">` | 同章 `<references>` 内必须有该 ID |
| `toc/item@id` | 应对应 `chapter_<id>.xml` 及其 `<chapter id>` |
| `toc/item@page_index,@order` | 应对应章节起始标题 block 的来源位置 |
| `chapter_head.xml` | `<chapter>` 应省略 `id` |
| `chapter_<id>.xml` | `<chapter id>` 应与文件名数字一致 |

当前校验器强制前三项和正文引用解析；TOC/章节 ID、文件名/章节 ID 及引用定义 ID 自身页码的语义对应目前由生产者负责。

## 归档与安全约束

`.pcex` 是普通 ZIP，pdf-craft 使用 Deflate 压缩写出。它没有额外的魔数、MIME 成员、整体签名或归档级校验和；格式识别同时依赖 `.pcex` 文件名和 ZIP 内容。

归档是未加密 ZIP，格式本身不提供密码、访问控制或其他保密机制。包内可能含有完整 OCR 正文、书目元数据、章节到原 PDF 的页码与 bbox、图片/表格/公式资源以及封面；复制、上传、存储或共享 `.pcex` 时，应按原文档同等的敏感程度保护它，并在格式之外配置适当的存储权限与传输加密。

加载归档时会在解压前检查：

- ZIP 结构和成员 CRC 是否损坏；
- 是否存在重复的 ZIP 成员名；
- 成员路径是否为相对、规范化的 POSIX 路径；
- 是否包含 `..`、反斜杠、绝对路径或符号链接；
- 是否只包含规定的根成员、章节文件和资源文件。

随后在临时目录中解压并执行内容校验。`open()` 在返回前建立并持有已校验快照，之后读取不再依赖源归档内容；`export()` 返回的新对象会在首次使用时按需打开刚写出的归档。临时目录和物化时机都是实现细节，调用方不应查找或依赖它们。

当前实现没有归档大小、展开后大小或压缩比上限，也没有内容签名；对于不可信来源，调用方应在进入 pdf-craft 前额外限制文件大小和来源。格式版本只解决结构兼容性，不提供真实性或防篡改保证。

## v1 校验明细

`PDFCraftExtraction.open()` 会立即执行以下校验：

1. 路径是现有 `.pcex` 普通文件；
2. ZIP 可读取、路径安全、成员不重复且成员集合受支持；
3. `manifest.json` 和 `pages.xml` 存在且符合各自字段约束；
4. `chapters/`、`assets/` 解压后存在并且只含合法名称的普通文件；
5. 可选 `toc.xml`、`cover.png` 的成员类型正确；
6. 所有章节 XML 与可选 TOC XML 可解析，根元素正确且核心字段可解码；
7. 章节页引用、bbox 和带 hash 的资源引用有效。

以下内容不是当前 v1 校验承诺：

- TOC 页码与 `pages.xml` 的对应；
- TOC ID、章节文件名和章节根 ID 的唯一性及对应；
- `order`、`level` 的范围或相邻连续性；
- 引用定义 ID 的唯一性及 ID 自身页码的存在性；
- 资源文件内容与文件名 SHA-256 的一致性；
- PNG 文件内容有效性；
- 孤立资源检测；
- 章节 XML 上每一处未知属性、重复可选子元素或无语义的额外结构；
- ZIP bomb 限制、数字签名或来源认证。

章节 XML 当前通过对象解码器而不是 XSD 做结构检查：必需字段、被解码的 payload 和页面映射会检查，但某些未被读取的额外结构可能被忽略，经过变换后也可能丢失。自行生成 `.pcex` 时不能把这些未校验项视为扩展机制或可随意违反的规则；后端只保证按照本参考列出的结构解释内容。

## analysis 工作区边界

传入 `analysing_path` 时，一次 PDF 提取的工作区可能类似：

```text
analysing/
├── extraction/                 # 内部、未压缩的 PDFCraftExtraction 工作表示
│   ├── manifest.json
│   ├── pages.xml
│   ├── chapters/
│   ├── assets/
│   ├── toc.xml
│   └── cover.png
├── ocr/                        # 诊断/断点缓存，不属于 PDFCraftExtraction
└── plots/                      # 可选诊断图，不属于 PDFCraftExtraction
```

完整转换为避免无意义的 ZIP 往返，会在进程内部直接使用 `analysing/extraction/`。这不把目录变成公开输入格式：用户在独立调用后端时仍应传入 `.pcex` 文件或已打开的 `PDFCraftExtraction` 对象。

后端不得依赖 `ocr/`、`plots/` 或其他 analysis 信息。需要长期保存、跨进程或跨机器传递时，应通过 `extraction_path` 得到 `.pcex`，而不是“领走”整个 analysis 目录。

## PDF 写回的额外条件

`.pcex` 保存文本位置，但不保存原 PDF 页面，也不保存源 PDF 的哈希或身份标识。调用：

```python
craft.patch_pdf_with_extraction(
    "original.pdf", "translated.pcex", "translated.pdf"
)
```

时，调用方应提供生成该 extraction 的同一份原 PDF。当前实现会在写回前确认：

- `pages.xml` 非空；
- extraction 记录的页码没有超过输入 PDF 页数；
- 每个章节 paragraph block 都能在 `pages.xml` 中找到页面几何。

它不能证明输入 PDF 与 extraction 来自同一源文件。若页数相同但页面内容或排序不同，仍可能把文字写到错误位置。单纯渲染 Markdown/EPUB 不需要原 PDF。

## 版本兼容

当前格式版本为 `1`。读取器只接受 `manifest.json` 中 `format_version: 1`，不对未知版本做降级猜测。新增可选 ZIP 成员或 manifest 字段也会被 v1 读取器拒绝，因此任何结构扩展都应配合新的格式版本和读取器实现发布。

应用程序若只需要后续渲染或翻译，应让 `PDFCraftExtraction.open()` 负责版本和完整性检查，不要仅凭 ZIP 可解压就认定包可用。
