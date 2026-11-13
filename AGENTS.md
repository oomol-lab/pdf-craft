# Agent 工作流

pdf-craft 是一个把扫描书籍 PDF 转换为 Markdown 或 EPUB 的 Python 库。本仓库使用 `~/.agents/skills/vibecoding` 作为通用维护工作法。本文件只记录 pdf-craft 特有的边界和按需阅读路由。

## 工作区边界

- `pdf_craft/` 是包源码。公共导入从 `pdf_craft/__init__.py` 暴露，便利入口在 `pdf_craft/functions.py`。
- `tests/` 包含轻量单元测试和小型 PDF fixture。普通代码改动默认以这些测试作为验证面。
- `docs/` 和 `README.md` 是读者/贡献者阅读的文档。不要把这些说明复制到 Agent 文档里。
- `references/` 是 Agent 面向的引用文档。只阅读当前任务需要的引用文档。
- `pdf_craft_tool/` 是未发布的本地 CLI，承载手动转换、翻译和冒烟矩阵；`scripts/` 只保留依赖源码同步辅助脚本。不要把它们当作默认开发流程。
- `analysing/`、`pdf-craft-output/`、`models-cache/`、`.venv/`、`dist/`、`build/` 和 `*.egg-info` 是生成产物或本地运行产物。

## 仅在需要时阅读

- 当需要判断模块归属、公共 API 边界或新代码应放在哪里时，阅读[架构与模块边界](references/architecture.md)。
- 当修改 PDF 提取、OCR 归一化、缓存 XML 产物、目录生成、章节生成、Markdown 渲染或 EPUB 渲染时，阅读[转换流水线](references/conversion-pipeline.md)。
- 当修改 PDF 回填、QTextLayout、字号拟合、行数/单行规则或 PDF 文本层排版时，阅读[两阶段 PDF 文本排版](references/pdf-text-layout.md)。
- 当选择 setup、验证、worktree 行为、发布或外部依赖处理方式时，阅读[开发与 Worktree](references/development-and-worktrees.md)。
- 当准备发版、更新版本号、编写 changelog 或调整发布流程时，阅读[发版流程](references/release-workflow.md)。

- 当修改 PDF 提取调度、页精修或内容回调时，阅读[流式设计与取舍](docs/design/pdf-extraction-streaming.md)。
- 当修改页精修协议、候选校验、采用、跨页组装或相关回归时，必须阅读[页精修验收的单页硬边界](references/page-refinement-validation.md)。

- 当修改翻译提示、请求聚合、公式对应、XML fill 或翻译验收时，必须阅读[翻译与 fill 的职责硬边界](references/translation-fill-boundary.md)。
- 当构造任何 LLM 的 XML 输入或处理 XML 输出时，必须阅读 [LLM XML 编解码边界](references/llm-xml-boundary.md)。
- 当修改失败恢复、翻译缺失、错误定位或任务成功判断时，阅读[失败与有效成果](references/failure-and-completion.md)。
- 当修改 PCEX schema、字段语义、格式版本或历史数据读取时，必须阅读 [PCEX Migration 边界](references/pcex-migrations.md)。

## 项目特有默认规则

- 所有 LLM 的 XML 输入必须经过共用 friendly XML 编码器，XML 输出必须经过共用 friendly XML 解码器再进入业务处理；嵌在批请求或 JSON 字符串中的 XML 同样适用。不得用标准 XML 序列化、直接拼接或局部实体替换绕过模型边界。此规则不要求非 XML 流程改用 XML，也不对自由翻译输出增加 XML 格式要求或门禁。细则见 [LLM XML 编解码边界](references/llm-xml-boundary.md)。

- 翻译工程层只有一个生成 XML 的有限 fill Loop：直接接收整组自由译文与来源结构，在 XML 中完成归属、公式身份和文字落位。禁止独立 JSON 分配、end/after 或换名的前后置模型阶段；不得为了沿用逐单元接口增设模型调用，也不得把工程 JSON 藏进批外壳。

- fill 的直接 inline 子树只允许在真实来源树中同祖父节点的兄弟 block 间按请求内 ID 平级迁移，不得跨祖父、跨层级或跨受保护资源边界；不得把请求聚合根当作真实祖父节点。身份绑定与属性恢复必须保留模型选择的合法位置，细则见[翻译与 fill 的职责硬边界](references/translation-fill-boundary.md)。

- 翻译 LLM 负责表达，fill LLM 负责工程落位。艺术家输出不得经过内容审查、拒绝或重译；只允许共用非法字符清理及可调整的 Markdown 纯文本工程视图。禁止扫描译文、生成公式占位符/身份表或按语法猜测对应；来源结构可以准备稳定身份和资源。全文验收只比较归一后的内容。结构门禁必须对应具体交付损坏，fill Loop 直接验收模型原始 XML 的结构及归一后的内容，不得先替换来源内容或物化后再让模型修复程序产生的差异。来源公式复用和格式物化在 Loop 成功后执行，只保全已接受内容；工程错误不得反馈为模型修复，也不得以脱离当前树的节点替换文档根。Narrative 中的独立公式与正文平等进入阅读流，不冻结、不复制成只读背景、不要求返回空壳；图片/表格资源锚点仍只读，展示文字保留独立 block 通道。修 bug 不得通过叠加协议、门禁或模型阶段强迫模型迁就内部算法。完整约束见[翻译与 fill 的职责硬边界](references/translation-fill-boundary.md)。

- 页精修的程序验收、纠错和采用只能拿到精修后的当前页完整结果，拿不到任何邻页，也拿不到精修前的当前页结构。精修前的页面结构只提供给 JEV/LLM；输入桥接层只负责运输，不授予程序语义验收权限。禁止以精修前结构、邻页原文/分类/评分/精修或采用结果及其派生语义来否决或改写当前页；模型请求不得依赖邻页评分或修复结果。组装只落实当前页连接声明，禁止以跨页一致性、试装后继、安全回退或保护已采用成果为名恢复邻页门禁，禁止跨页反馈重修。这是不可让步的架构约束；现存相反代码属于待移除偏移，不构成例外。细则见[单页硬边界](references/page-refinement-validation.md)。
- 页精修采用后的组装、分章和恢复只物化提交决定，不再按字号、标点或相邻文字重算段落语义、标题级别或猜测断词。启发式处理放在 JEV/LLM 前，保留原始来源和可撤销的工程建议；模型采用后不得再隐式重跑。
- 新增或修改的模型提示正文必须维护在 `.jinja` 模板中，不得内嵌在 Python 字符串里。提示说明统一使用英文，原始文档证据保留原语言；模板应是可直接阅读的正文，避免 JSON 拼装与宏函数库。Python 负责模板选择、上下文、请求编排和结果处理；JEV 模板的位置见[结构审核设计](docs/design/jev-structural-review.md)。
- PCEX 业务代码只处理当前格式。历史格式兼容必须集中在 `pdf_craft/document/migrations.py`，在输入物化边界完成；禁止在翻译、内容流、渲染器中按字段缺失或旧形态添加 fallback。字段语义改变必须升级 PCEX 格式版本并同步迁移源章节与译文层，不能以功能测试通过代替这一架构要求。
- 这是库项目。除非未来任务引入长期服务，否则不要启动常驻开发服务器。
- 普通验证应避免 CUDA、模型下载、网络请求和完整 PDF 转换，除非任务明确触及这些行为。
- 模型缓存和转换输出不得进入提交内容。`pdf_craft_tool` 的产物默认写入每个 worktree 自己的 `pdf-craft-output/`；只有任务明确需要 OCR 时，才考虑共享外部模型缓存。
- 纯文档任务不得修改包代码或依赖版本。
