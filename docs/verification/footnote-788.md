# Footnote contract refactor (#788)

## 修复前证据

基线为 `3062f55`（含 #508）。在修改包实现之前新增并运行
`tests/test_footnote_contracts.py`，8 个测试方法产生 8 个断言失败和 1 个异常
（F7 的两个字段分别失败）。完整原始输出见 [footnote-788-red.txt](footnote-788-red.txt)。
测试与失败记录先提交为 `8d4facf`，随后才开始修改实现。

| 问题 | 同一业务回归测试 | 修复前真实结果 | 修复后的正确行为 |
| --- | --- | --- | --- |
| F1 | `test_f1_ocr_streams_have_distinct_persisted_fragment_identities` | 两个 fragment 只有一个身份：`2 != 1` | OCR 两流共享持久化身份空间，保留流内下标 |
| F2 | `test_f2_cross_page_identity_change_is_rejected_before_acceptance` | 任意改名直接通过，重试记录 `[0]` | guaranteed JSON 业务编译拒绝，第二候选通过；START/TAIL 都在 |
| F3 | `test_f3_star_definition_and_occurrence_survive_chapter_encoding` | 编码后 0 条定义 | 星号有正文引用及定义，脚注文字完整 |
| F4 | `test_f4_reclassification_restores_detached_printed_mark` | 只有 `Footnote.`，丢失 `① ` | 改判回正文得到精确的 `① Footnote.` |
| F5 | `test_f5_reclassification_preserves_source_reading_position` | 顺序 `[0,70,35]` | 顺序 `[0,35,70]`；另外测试多栏和跨栏通宽块 |
| F6 | `test_f6_repeated_occurrences_can_round_trip_without_changing_definition` | `REF_CITATION_BIJECTION` 异常 | 两个出现指向同一恢复后的定义对象 |
| F7 | `test_f7_missing_metadata_is_repaired_inside_guaranteed_json` | null role/level 都直接通过，重试记录 `[0]` | 编译阶段分别拒绝并重试，最终可以真正组装 TextFlowItem |
| F8 | `test_f8_pdf_replacements_include_translated_footnote_once` | 只有 `Translated body①①` | 正文与一份脚注 replacement 都存在，并恢复印刷标记 |

原回归断言没有删除、跳过或以抛异常替代正确行为。仅适配移除后的内部 reconciliation 调用、
补充类型收窄及正常 FlowItem 组装；F8 的期望进一步加强为 `① Translated note`，要求写回脚注时
同时恢复印刷标记。`tests/test_footnote_assembly.py` 的真实 PDF 用例还验证归档重开、coverage、
文本擦除与字体填充后的可提取译文，脚注出现恰好一次，原正文/原脚注均不再残留。

## 最终业务模型

1. **来源与结论分离。** 在传统正文/脚注分析前建立 `TextSource`，保留完整文字、原分类、
   OCR 流及流内下标、独立阅读位置。fragment 继续拥有稳定 page/source_order 与 bbox。
   正文 source_order 不变，脚注身份加正文布局数偏移；OCR Page 不改编号。
2. **可逆语义投影。** 印刷前缀记录为源文字范围；改判正文保留完整前缀，组装定义才移除
   实际匹配的范围。段落断词修复在 ownership 组装之后应用于语义副本，原始记录不变，空的
   投影片段仍保留身份和几何。阅读顺序保留正文栏序；同栏误分类块按该栏位置插入，通宽块
   分隔阅读带，不用全页 y 排序交错多栏。
3. **定义、出现、标记独立。** 定义可有零个或多个引用，星号与圈号共用匹配规则。
   `AnalysedCitation.state` 明确区分 definition / continuation / unresolved。
   已知 continuation 缺少起点即失败；unresolved 内容保留为可读正文。未匹配定义由
   `Chapter.definitions` 保存，通过现有 `<references>` 输出，不制造虚假 ref。
4. **一条连接一个真源。** 只编辑后继的 `continues_from_previous`；outgoing 为派生视图。
   兼容接收旧 outgoing 字段时，它仅作同页一致性断言，不决定跨页连接。
   移除了 last-repaired-page-wins 路径。每个候选在完整暂存图中组装校验；候选被接受时才
   原子更新暂存图，全部请求成功后返回，失败或取消不修改调用方输入。
5. **受控身份编辑。** 既有 ID 不可任意改名；新增页内定义用 `new-页码-index`，页内非共享
   定义可重分组拆分/合并。跨页共享起点和已有片段不能被单页请求删除/重命名或重编索引；
   unresolved 尾段可显式连接到上一页已有定义，禁止猜测未知前驱。拆分合并保留非定义起点
   上的内部编号，避免把正文序号错误删除。
6. **统一消费来源。** 传统算法与 LLM 结论最终走同一恢复/校验入口。
   `narrative_texts` 统一枚举正文及定义文字，供 coverage、归档校验及 PDF 计划使用。
   PDF 按物理来源去重，恢复定义首段标记；未翻译的脚注仍为障碍。通用 XML translator
   无需感知 PCEX 脚注，#508 回写位置回归继续保留。

拆分/合并、孤立续段、跨页起点删除、续段明确连接、多栏、完整来源与来源身份、ownership
双向改判、1–6 个星号、重复出现、未匹配定义、非法元数据、ignored 的派生字段及实际 PDF
填充，均由离线业务路径测试覆盖。测试不调用 LLM/JEV、OCR、CUDA 或模型下载。

## 兼容决策

没有修改公共门面、配置 API、包版本或依赖。PCEX format_version 仍为 4，chapter XML 仍为
原有 v3 flow schema；新来源记录和分析关系只在提取内存中使用。未引用定义已可由现有
references 容器表达，不增加 XML 字段。coverage 使用既有 paragraph 身份记录脚注正文。

读取、导出已有 PCEX 或已有译文层时不重新编号。v1/v2/v3 的既有适配及 v4 的 source/译文
结构对应验证继续运行；缺失 coverage 仍表示 preserved，不推测旧脚注已经翻译。
同章重复 fragment 身份的旧歧义归档仍明确拒绝，不能凭 OCR 分类或 bbox 猜测无损迁移。

## 检查与在线验收

最终检查全部通过：

- `.venv/bin/python test.py`：805 tests，OK。
- `.venv/bin/python -m pytest -q`：833 passed。50 条 warning 全部来自已有 pypdf
  `replace_contents` 的第三方弃用提示。
- `.venv/bin/python -m pyright --pythonpath .venv/bin/python pdf_craft tests`：0 errors / 0 warnings。
  显式 Python 路径确保使用 worktree 依赖。
- `.venv/bin/python -m pylint pdf_craft tests`：退出码 0，10.00/10。
- #508 的普通 XML/XHTML/PCEX inline ref 分段、校验、回写测试包含在全量检查中。

2026-10-19 完成两次真实 PDF 翻译：首轮调用 `translate_pdf`，输出 26 页；最终验收通过
`translate_extraction` 保存 v4 层，再用 `patch_pdf_with_extraction` 写 PDF。两轮都使用
worktree `.env`、独立输出目录及已提交的 26 页 OCR 缓存，没有重新 OCR 或下载模型。
正式配置为 `FootnoteOptions(refinement=FootnoteRefinement(jev=..., llm=..., executor=...))`，
没有使用 CLI `--footnotes` 冒充 refinement，没有使用 pinned JEV baseline。

最终验收的 JEV 26 页均在线执行。沿用公共配置默认 `risk_threshold=0.70`，对应
`p_pass < 0.30` 才进入 LLM 修正；选中页为 2、4、6、12、20、26。
模型为 `oomol-chat`。独立新目录的请求日志记录：脚注修正 6 次请求 / 6 次成功；文本翻译
135 次请求 / 135 次成功（另有同次运行内 36 次 cache hit）；填充 314 次请求 / 314 次成功。
没有以原文回写或关闭错误校验替代实际翻译。

结果与检查：

| 验收项 | 结果 |
| --- | --- |
| PDF 页数 / 大小 | 26 页 / 9,924,597 bytes |
| PDF replacement | 123 个，其中正文 91、脚注段落 32 |
| 脚注实际写回 | 32/32 段的译文开头文本探针均能从对应 PDF 页提取 |
| 引用与定义 | 源与译文各 22 处 inline ref、22 条定义；fragment/ref 的属性和顺序完全对齐 |
| v4 源内容 | 8 份 source chapter XML 与原始 source.pcex 逐字节一致 |
| 归档 | translated.pcex 打开、结构校验及重导出通过 |
| Markdown / EPUB | 译文渲染通过；EPUB 10 个 XML/XHTML/OPF/NCX 成员全部可解析 |
| 原错误现场第 7 页 | 已渲染图片目检：正文与脚注均为译文，正文圈号与脚注首标记保留，未观察到重叠 |

EPUB 的附加验收首次使用目标语言字符串 `English` 自动选择渲染语言，被现有
`zh/en` 约束拒绝；按公开参数显式传 `lan="en"` 后通过，没有修改该不相关 API。
这次拒绝发生在 PDF 翻译、写回及脚注 replacement 核验全部完成之后。
在线首轮还暴露了 omitted outgoing 字段在 ignored layout 上继承旧值的问题，已修复，并由
`test_ignored_layout_does_not_inherit_a_derived_outgoing_edge` 覆盖，非法跨页前驱则仍被拒绝。

验收运行开始后补充的通宽来源带规则在 citation_large 的全部来源 reading_order 上与运行
快照逐项一致；该文件也没有受影响的跨 fragment 拉丁断词边界。最终离线检查针对全部提交内容。

运行产物和请求日志留在忽略目录：

- `pdf-craft-output/footnote-refactor/`：red/focused/full tests、pytest、pyright、pylint 日志。
- `pdf-craft-output/footnote-788-live/`：首轮 translate_pdf 的 PDF、JEV 结果和请求日志。
- `pdf-craft-output/footnote-788-acceptance/`：最终 source/translated/reexported.pcex、
  citation_large-en.pdf、translated.md、translated.epub、page-7.png、
  `replacement-evidence.json`、`result.json`、`run.log`、`verify.log` 及三个阶段的日志。

没有提交模型缓存、PDF 或凭据。结构、计划及全文测试是确定性验收；32 个 PDF 文本探针和
第 7 页目检不等于逐字审校全部译文的语言质量。
