# 两阶段 PDF 文本排版

**约束范围：** PDF 翻译回填的 QTextLayout 排版、字号选择、跨 bbox 连续流、页面级字号归一化与 headline 规则。**不约束：** OCR 识别、擦除底图、翻译 XML、PDF 交互层或公式内容本身。**何时阅读：** 修改 PDF 回填、QTextLayout、字号拟合、行数/单行规则或 PDF 文本层排版时。

## 核心模型

PDF 回填是两个嵌套的流式阶段，不能把它们混为同一个“字号选择”步骤：

1. **第一阶段是 TextFlowItem 级操作。** 输入一个完整 TextFlowItem 的连续 text-fragment bbox，输出统一字号及跨 bbox 的初始文字流；TextFlowItem 是 v2 术语。
2. **第二阶段是页级操作。** 只有该页不再会被任何未关闭的第一阶段 TextFlowItem 触及时，才读取该页的初始结果，并为每个 bbox 独立计算最终字号和绘制位置。

TextFlowItem 可以跨页。因此窗口关闭不是“读完一个页面”就发生：必须等所有可能继续流入该页的 TextFlowItem 已在第一阶段关闭。窗口只保留当前可触及页面的排版数据；不得为了页级归一化把整本书的 Qt 行、页面 raster 或完整几何留在内存。嵌入图表是障碍区，不属于可填文字 bbox；DisplayFormula 是独立流节点。

## 第一阶段：局部连续流

第一阶段的唯一操作单元是 TextFlowItem，不区分 `text`、`heading` 或任何视觉身份。

- 该 TextFlowItem 的所有 bbox 组成连续文字流；其字号在第一阶段必须统一。
- QTextLayout 独占 shaping、断行、双向文字与语言排版。pdf-craft 不能用字符数、手写分词或自定义断行替代它。
- 对一个候选字号，bbox 的 width 决定 Qt 每行可容纳的文字；bbox 的 height、aim line 和上下 forbidden line 决定可选择的虚拟行槽。Qt 自然换行是正常结果，不是失败。
- 第一阶段在可行字号中寻找尽量贴近各 bbox aim line 的方案，同时不得越过真正的 forbidden line。bbox bottom 是目标线，不自动等同于禁止线。
- 这一阶段不得读取同页其它 TextFlowItem 的字号；不得计算等级平均；不得施加“headline 至少为正文多少倍”的规则；不得因文字看起来像标题而改用自然宽单行分支。

第一阶段开始前不存在“单行文本”的业务概念。OCR 保存的是 block bbox，不保存可被可靠复原的原始行级排版；原文紧贴字形的高度和编辑时的真实行盒不是同一件事。

## 第二阶段：页面类型尺度

当一页关闭后，第二阶段使用该页第一阶段的已冻结 placements：

- 按 `(layout_ref, layout_level)` 分组，计算各文字等级的加权平均字号。权重是 bbox 实际承载的字符数，而不是整个 TextFlowItem 剩余文本。
- 普通 bbox 以该等级的目标字号为方向，在保持第一阶段已分配文字和行数的前提下重新测量。后续 bbox 可以与同一 TextFlowItem 的其它 bbox 使用不同最终字号。
- 只有第一阶段结果已经是一行、且 TextFlowItem 仅有一个 bbox 的 placement，才是“孤立单行”候选。它的 OCR height 常是紧贴字形的裁剪边界；第二阶段可保持一行并从外部等级尺度重排，允许水平方向自然延展。它不是“原文一行”的猜测。
- 孤立单行样本默认不参与本等级的平均字号，避免窄 OCR bbox 污染统计；若排除后没有样本，才回退到包含它们的全量加权平均，避免 0/0。

## Headline 规则

`heading` 的语义只在第二阶段生效：

1. 先完成并归一化正文 placements，得到本页最终正文尺度。
2. 由正文尺度和 `headline_min_body_ratio`（或相应等级配置）推导 headline 的最小字号；没有正文时才使用 headline 的显式 fallback / style minimum。
3. headline 的第一阶段自身字号与等级目标都是候选，但最小字号是下限而非固定字号：局部几何本来可支持更大字号时，不应为了平均值缩小它。
4. 若 headline 在该下限下无法保持第一阶段已裁定的行数和正常几何，则以 bbox 左侧垂直中点为锚点，用 Qt 真实字体度量绘制一条自然宽单行，向右延展。超过 OCR bbox、下方内容或页面可见范围属于既定业务结果，不应写成 warning、窄宽多行强制写入或整页失败。

第一阶段的 `force_written` 仅是“局部几何未容纳”的恢复标记，不是第二阶段的字体裁决。若它出现在 headline 上，第二阶段必须用完整已分配文本重建上述自然宽单行，不能保留小字号的强制多行 placement。

这条最终溢出规则只属于 headline 的第二阶段。它不改变第一阶段允许标题自然换行的事实，也不为普通正文创建标题专用分支。

## 公式、字体与绘制

行内公式在 QTextLayout 中是不可截断的 proxy span；最终绘制可替换为 PDF 向量公式并保留 ActualText。无论是孤立单行还是 headline 溢出，都必须重新以最终字号度量公式；公式渲染失败仍遵循既有 plain-text fallback。

用户配置字体族，Qt 负责字体 fallback 与逐字 glyph fallback。排版器必须使用 Qt 的实际 font metrics，不得用近似字宽推导字体大小或行数。

## 不变量与验证

- 擦除和填充保持代码边界分离；填充 bbox 与擦除 bbox 可以不同。
- 仅第二阶段可以使用页面级信息；仅第一阶段可以在多个 bbox 之间转移 TextFlowItem 的连续文字。
- 普通布局的 width、行数与 forbidden line 不能因二阶段平均而被破坏。
- headline 最小字号无法正常容纳时仍必须输出自然宽单行，不能退化为窄 bbox 内的多行 forced write。
- 修改此区域时至少覆盖：普通跨 bbox 段落、孤立单行统计回退、headline 下限、以及一个真实 PDF 回填页面的视觉检查。
