# 跨章节单组 XML fill 回归

PCEX 与 EPUB 的章节流连续进入预算分组。每组一份自由译文，直接进入一个生成完整
XML 的有限 fill Loop；不生成分配 JSON、end/after 表或外层业务 JSON 批响应。
同一回答表达来源 ID、公式源身份和文字落位，程序验收整组全文、结构及身份后再采用。
不同组可并发推进；同组失败不部分采用，也不重新翻译。

组预算以实际 XML token 估算，不额外给每个 ID 加固定惩罚；每组最多 64 块。
来源收集最多 64 根，输入暂停 200ms 或持续收集 1 秒后可交付尾组，未完成读取保留。
操作级 window 和共享 executor 限制实际请求并发；不预载全书。
XML 输出预算覆盖模板、完整译文与身份标记。首次与修复使用同一原译文和预算。

在配置好依赖的待审分支运行：

```sh
python -m pytest -q tests/test_translation_grouping.py tests/test_request_batch.py tests/test_single_xml_fill.py tests/test_elastic_formula_restore.py tests/test_online_formula_allocation.py tests/test_canonical_math_projection.py tests/test_llm_xml_boundary.py
```

这些离线测试只替换模型传输，保留真实分组、fill 校验、章节回填、coverage 与 EPUB CFI。
24 段分别放一章、六章、每段一章，直接限制物理请求总数并核对所有输出；另有
57 段/11 章含 metadata/furniture 的门禁、137 段稠密/碎片章节、有界准入、输入暂停、
取消、跨组修复并发及整组原子采用。恒等内容的确定性填充经过相同校验，不能伪造计数。

线上局部的无网络重放见 [service #364 回归](service-364-allocation.md)。
四个 e81368d 付费失败组可按固定原始日志离线重放：

```sh
PDF_CRAFT_PAID_FILL_FIXTURES=/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft-main-e81368d-smoke-20261109/pdf-craft-output/paid-full-deepseek-20261109T051229Z python -m pytest -q tests/test_paid_fill_replay.py
```

此命令使用原始来源文本和完整译文重建工程局部，每组只允许 1 次艺术家调用和
1 次完整 XML 工程回答。两个 EPUB 局部走真实 EPUB 归档、章节回填、CFI 回调并核对
导出的各 XHTML 内容与身份；公式组逐单元比对完整渲染文本、公式顺序和值及来源位置。
仅在测试中从旧证据准备参考 XML，生产路径不接受旧 JSON。

`test_single_xml_fill.py` 还包含公开裁切的段落空白/重复来源条件；旧 JSON 回答
只作为失败来源证据，新模型替身返回完整 XML，不再尝试修补旧分配协议。

需要显式授权并配置模型的完整付费门禁：

```sh
python -u -m pdf_craft_tool.smoke.paid_full --output pdf-craft-output/NEW-REVIEW-DIRECTORY --profile default --ocr-backends deepseek-ocr-vendor
```

在固定待审提交新跑全部 14 组，仅第一代 DeepSeek OCR vendor；保留 EPUB 与公式等
无 OCR 测试。公式专项必须完整通过既定三轮。日志保留真实请求、XML Loop 反馈、
各组产物和 report.json，不能用跳过、降级或离线替身代替付费门禁。
