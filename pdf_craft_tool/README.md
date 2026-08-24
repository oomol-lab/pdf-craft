# pdf-craft 本地开发 CLI

`pdf_craft_tool` 是仓库内的开发、验收与手动转换工具，不包含在发布的
`pdf-craft` Python 包中。它只通过 `pdf_craft` 的公共 API 组合 Extractor、
Renderer、Pipeline 和 Transformer。

从仓库根目录运行：

```shell
poetry run python -m pdf_craft_tool --help
```

先复制 `.env.template` 为 `.env`，一次性填写全部六种 `PDF_CRAFT_*` OCR 配置；切换
backend 时不需要再修改 `.env`。翻译
翻译或可选的 TOC 层级增强需要文本 chat-completion LLM profile；OCR-only endpoint
不能代替它。默认 profile 使用本机 `oo llm config --json` 提供的 OOMOL 连接，凭据不写入 `.env`。

## OCR backend 配置与选择

`.env.template` 为以下六种 backend 分别保留配置分组：

- `deepseek-ocr-local`
- `deepseek-ocr2-local`
- `unlimited-ocr-local`
- `deepseek-ocr-vendor`
- `deepseek-ocr2-vendor`
- `unlimited-ocr-vendor`

`PDF_CRAFT_OCR_MODE` 只是未传参数时的默认选择，不会启用、清空或覆盖其他 backend。
所有 PDF CLI 命令和 `smoke run` 的 `--ocr-mode` 都可显式选择其中一个 backend；矩阵在每个
run 的 `backend` 字段中选择。local backend 使用自己的模型缓存、offline 和可选 CUDA device
配置；vendor backend 使用自己的认证和 endpoint 配置。

`deepseek-ocr2-local` 的本地实测路径使用 `--ocr-size base`；不要把 `tiny`
当作该 backend 的可靠默认 preset。CLI 和库层都会在显式使用
`deepseek-ocr2-local` + `tiny` 时给出清晰错误。

## 工作目录

`pdf extract`、`pdf convert` 和 `pdf translate` 每次都会创建独立的工作目录，
默认位置是 Git 忽略的 `pdf-craft-output/manual/`。目录以来源、操作、日期和当日
序号命名，例如 `citation-convert-20260822-001/`；同一次调用绝不会覆盖已有目录。
`package render` 和 `epub translate` 也使用此规则。通过 `--work-dir` 指定位置时，
目录不存在则创建，已存在则复用。PDF 命令会在工作目录内记录来源 PDF 和 OCR
设置，防止不同输入或不同 OCR backend 错误复用已有 OCR 缓存。工作目录保存中间
`package/`、翻译缓存和日志，方便人工检查、恢复或后续单独渲染。

所有 `--pages` 参数使用从 1 开始的 PDF 页码，例如 `--pages 1,2,3`。

## PDF 与 Package

```shell
# PDF -> 可复用 DocumentPackage
poetry run python -m pdf_craft_tool pdf extract tests/assets/citation.pdf \
  --ocr-mode deepseek-ocr-vendor --pages 1 --work-dir pdf-craft-output/citation-extract

# Package -> Markdown 或 EPUB；此命令不需要 OCR 配置
poetry run python -m pdf_craft_tool package render pdf-craft-output/citation-extract/package \
  --format markdown --work-dir pdf-craft-output/citation-render

# PDF -> Markdown 或 EPUB
poetry run python -m pdf_craft_tool pdf convert tests/assets/citation.pdf \
  --ocr-mode deepseek-ocr-vendor --format epub --pages 1,2,3
```

PDF 提取参数可在以上三个 PDF 命令中组合使用：

```text
--pages --ocr-size --dpi --max-page-image-file-size
--max-ocr-tokens --max-ocr-output-tokens
--cover --footnotes --plot --toc-assumed
```

`--ocr-mode` 覆盖 `.env` 内的 `PDF_CRAFT_OCR_MODE`；不传时使用 `.env` 的默认值。
它只决定本次运行使用哪个已配置 backend，不会改写 `.env`。

## 翻译

```shell
# PDF -> 已替换的译文 EPUB
poetry run python -m pdf_craft_tool pdf translate tests/assets/citation.pdf zh \
  --format epub --submit replace --ocr-mode deepseek-ocr-vendor --pages 1

# PDF -> 原文块后追加译文块的 Markdown
poetry run python -m pdf_craft_tool pdf translate tests/assets/citation.pdf zh \
  --format markdown --submit append-block --ocr-mode deepseek-ocr-vendor --pages 1

# PDF -> 替换式 PDF patch；PDF 不支持 append-block
poetry run python -m pdf_craft_tool pdf translate tests/assets/citation.pdf zh \
  --format pdf --submit replace --ocr-mode deepseek-ocr-vendor --pages 1

# EPUB -> EPUB，支持 replace 与 append-block
poetry run python -m pdf_craft_tool epub translate tests/assets/epub/Cambridge.epub zh \
  --submit append-block
```

翻译命令可用 `--prompt`、`--max-retries`、`--max-group-tokens` 和 `--concurrency`
控制 XML Translator；通过 `--translation-llm PROFILE` 和 `--fill-llm PROFILE` 选择 profile。
两者相同（默认都是 `translation`）时复用同一个 `LLM` 对象。`pdf translate --format pdf`
只允许 `--submit replace`。PDF 提取命令还可通过 `--toc-llm PROFILE` 使用 LLM 改善目录层级判断。

## 冒烟矩阵

先查看全部真实样本：

```shell
poetry run python -m pdf_craft_tool smoke assets
```

`smoke run` 将一条参数化通路写入 Git 忽略的 `pdf-craft-output/smoke/` 下独立目录。
目录同样以来源、route、日期和当日序号命名。通过 `--output-root DIR` 可替换这个根目录。
目录包含
`manifest.json`、`checks.json`、`logs/`、提取的 `package/` 和渲染产物；凭据会
从报告和 traceback 中脱敏。

smoke 命令的进程退出码与报告状态一致：`passed` 和 `planned` 返回 0，`failed`
或 `skipped` 返回非 0。矩阵命令会聚合所有 run，只要有一个 run 失败或跳过就返回
非 0，避免 CI 或 Agent 把未执行的必需路径误判为成功。

```shell
# 先确认参数展开和产物目录，不调用 OCR
poetry run python -m pdf_craft_tool smoke run \
  --asset citation.pdf --route markdown --ocr-mode deepseek-ocr-vendor \
  --pages 1 --ocr-size tiny --marker '[translated]' --dry-run

# 运行真实 PDF -> Markdown 路径，并执行 Package / Markdown 检查
poetry run python -m pdf_craft_tool smoke run \
  --asset citation.pdf --route markdown --ocr-mode deepseek-ocr-vendor \
  --pages 1 --ocr-size tiny --marker '[translated]'

# 运行 EPUB 格式检查，不需要 OCR 或 .env
poetry run python -m pdf_craft_tool smoke run \
  --asset epub/Cambridge.epub --route epub-check

# 运行真实 EPUB 翻译路径，并检查生成的 EPUB
poetry run python -m pdf_craft_tool smoke run \
  --asset epub/Cambridge.epub --route epub-translate \
  --target-language zh --submit append-block
```

`--route` 可选 `package`、`markdown`、`epub`、`pdf-patch`、`epub-check`、
`epub-translate`，以及专门验证 Package renderer 分支的
`package-markdown`、`package-epub`。PDF route 可使用同一组限制参数：
`--pages`、`--ocr-size`、
`--dpi`、token 限额、图片限额、`--cover`、`--footnotes`、`--plot` 和
`--toc-assumed`。`markdown` 和 `epub` 使用 `--marker` / `--submit` 覆盖确定性的
Package 变换；`pdf-patch` 使用 `--patch-prefix` 检验 patch 产物和 geometry。
需要真实 LLM 翻译时，可以给 `smoke run` 传入 `--translation-llm-profile`
和 `--fill-llm-profile`；`epub-translate` 默认使用 `translation` profile。

对于需要稳定保存或批量执行的组合，使用 JSON 矩阵：

```shell
poetry run python -m pdf_craft_tool smoke matrix --config tests/smoke/minimal.json --dry-run
poetry run python -m pdf_craft_tool smoke matrix --config path/to/matrix.json
```

矩阵结构为 `{ "defaults": {...}, "runs": [...] }`；每个 run 的字段与
`SmokeRun` 一一对应。`tests/smoke/minimal.json` 是最小可运行示例。

仓库还提供了不包含凭据的 vendor/LLM 真实运行矩阵：

```shell
poetry run python -m pdf_craft_tool smoke matrix \
  --config tests/smoke/vendor_real.json
```

该矩阵读取当前工作区 `.env`，会产生真实 OCR 和文本 LLM 请求；执行前应确认
`PDF_CRAFT_OCR_MODE`、对应 vendor OCR 配置以及 `translation`/`fill` LLM profile
均已配置。local OCR route 在无 CUDA 环境中会记录为 skipped，不应伪装为 passed。

全部六种 OCR backend 的最小矩阵位于 `tests/smoke/all_ocr_backends.json`：

```shell
poetry run python -m pdf_craft_tool smoke matrix \
  --config tests/smoke/all_ocr_backends.json
```

该矩阵会对每个 backend 分别报告 `passed`、`failed` 或 `skipped`。无 CUDA 的 local
backend 应明确 `skipped`；不要把它当作 vendor backend 的失败或成功。
