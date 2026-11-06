# 跨章节翻译请求打包

章节决定归属，group 决定连续输入的预算窗口。首阶段将就绪单元的可读原文汇聚为
一个翻译请求，返回完整自由文本，不要求 XML/JSON、身份或占位符。
分配 fill 接收原始回答和各来源，在保持普通译文完整的前提下分配归属及公式对应；
其后的 XML fill 按各单元恢复结构，可批量使用仅属于工程层的 JSON envelope。

来源收集、64 单元上限、token 预算、window 背压及 EOF/暂停时交付保持有界。
PCEX 正文和脚注、EPUB XHTML 根连续进入同一调度流，不因章节或文件边界清空预算。
连续来源最多收集 1 秒，暂停 200ms 时先处理尾批并保留未完成读取；来源和就绪工作
均最多收集 64 个，每次最多准入 `64 * window` 个工作，避免大章节展开整书任务。
GroupRequests 收集本组就绪调用后才 flush，收集期间不占供应商执行槽位。
请求预算为 `max(4096, max_group_score * 4)`，仍预留实际输入的输出空间；
超预算的单个语义单元保持单请求，不为凑预算截断内容。
不同 operation、headers、usage collector、阶段和修复参数不混批。
各单元完成分配后仍独立执行 XML fill、采用、coverage 与回调。已成功单元不参与兄弟
单元的 XML 修复；分配循环固定同一份原始回答，不重新调用翻译模型。
分配和 XML fill 各有有限 fill 预算；调用层的真实网络故障按原传输预算处理。
取消单个等待者不取消同批兄弟，最后一个等待者负责取消并等待底层请求。

真实模型调用数包括自由翻译、分配 fill、XML fill 及其修复。恒等译文可以确定性分配，
单单元无公式回答直接进入 XML fill，不能伪造请求计数。失败日志保留本轮工程输入中的
完整原译文，模型缓存和客户现场不提交。PCEX 持久化结构及 EPUB CFI 不改变。

## 无网络回归入口

在仓库根目录运行：

```sh
.venv/bin/python -m unittest tests.test_translation_grouping tests.test_request_batch -v
```

`test_translation_grouping` 经由 PCEX `translate_extraction` 和原生 EPUB
`translate_epub`，只替换供应商 transport，保留 tokenizer、分组、运行时、canonical
fill、采纳及归档写回。相同的 24 段分别放一章、六章和每段一章，直接断言模型调用
总数（包括首阶段与 fill），并检查译文、coverage、章节归属和 EPUB CFI。
不使用“所有请求先同时到达”的屏障，也不替换 flush。

同模块还以生成的 57 段／2177 words／11 章样本固定包含 metadata 与 furniture 的
15 次调用门禁，CI 不依赖客户材料。还覆盖错开 20ms 的自然输入、慢供应商与单请求槽位、137 个单元的有界准入和
EOF 尾批、1000 段单章在首个请求前的资源展开上限、输入暂停时交付、取消关闭未完成读取，以及一个兄弟单元修复时另一个已采纳
单元及时交付。transport 层的身份、预算、缓存、usage、失败日志和取消测试继续独立执行。

真实 DEV 样本的本地门禁为
`test_private_production_case_includes_metadata_and_furniture`。它从以下固定私有目录读取
`source.pcex`，材料不存在时跳过，客户内容不进入 Git：

```
/Users/taozeyu/codes/github.com/oomol-lab/pdf-craft/pdf-craft-output/issue-932-1bc9aef5-investigation/
```

该门禁使用现场的 o200k_base、2600 分组预算、window=6，启用 metadata 和 furniture，
断言完整转换的真实 transport 调用不超过 15 次，并逐单元检查来源文本、公式、资产及
几何结构的往返。本轮离线运行观察到 15 次；这是正确模拟响应的调用计数，
不能代表实际模型翻译质量或保证所有文档的节省比例。

新门禁首先在旧生产入口上运行：24 个短段落发出 16 次调用，超过 4 次上限。
该失败来自实际 provider 调用数，不能用任务数、文件生成成功或一章/多章同样多代替。

完整轻量验证：

```sh
.venv/bin/python test.py
.venv/bin/pyright pdf_craft tests --pythonpath .venv/bin/python
.venv/bin/pylint pdf_craft tests
```

线上 EPUB CFI 原材料可选独立重放：

```sh
PDF_CRAFT_EPUB_CFI_REGRESSION=/absolute/path/to/original.epub \
  .venv/bin/python -m unittest tests.test_epub_task_context -v
```

私有现场不入库；真实模型质量及付费回归由后续实验评估。

## 自由翻译与公式回归

`tests.test_online_formula_allocation` 使用公开 table&formula.pdf 的两种 OCR 局部，
强制重排 `=1` / `=0` 并使分配 fill 首次失败；验证源节点、译文和只翻译一次。
`tests.test_source_math_identity` 还用空文本、任意标记和空白回答验证原样进入 fill。
单个 owner 只有公式容器时可确定性执行 XML fill，仍须通过同样的完整校验和身份恢复。

针对这两份局部的单轮付费复现（需显式配置模型）：

```sh
.venv/bin/python -m pdf_craft_tool.smoke.fill_formula_loop \
  --output pdf-craft-output/NEW-DIRECTORY \
  --fixture tests/assets/translation/online_formula_identity_cases.json --rounds 1
```

输出记录全部真实请求及分配请求数，保存最终 XML。公式多重集和几何往返只是基础检查；
还需检查工程绑定与最终公式的语义位置，尤其 `=1` / `=0`，不能只看报告 passed。
