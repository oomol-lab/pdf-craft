# 跨章节翻译请求打包

章节决定内容归属，StructuredTranslation 决定 canonical 验收单元，
RequestBatcher 决定模型请求。这三个边界互相独立。PCEX 正文、脚注、资产字段与原生
EPUB 共用 XMLTranslator 的两个请求入口：首阶段翻译和 fill。

组包不修改 PCEX 持久化结构、版本、coverage 或来源身份，也不更换 EPUB 的原节点与 CFI。
每个单元先得到独立 canonical 译文，再执行原有结构、Unicode、空白和公式验收。
同一批请求里的局部 XML ID、math-ref key 使用各自 conversation 命名空间；外层 JSON
显式携带请求内唯一 ID，输出允许重排。禁止按空行或正文拆分结果。

## 有界与失败语义

- PCEX 的真实调度入口把正文和脚注持续交给一个 Narrative/XML 任务流，
  不再按 ContentItem 单独调用 transform。原生 EPUB 的异步根使用相同的 splitter，
  不因读到下一个 XHTML 重新清空分组范围。
- 来源收集最多 64 个 XML 根，并以原 token 分组预算约束读前量。首个根到达后
  连续输入最多收集 1 秒；输入暂停 200ms 时保持那一次未完成读取，先处理现有尾批。
  逐根读取不会因整个收集耗时刚超过 200ms 就截断仍在连续到达的内容。
  大根仍由原 splitter 按预算拆分；每次最多准入 64 个语义工作，工作容量为 `64 * window`。整组
  一次准入最多额外占用 63 个工作位；成员独立释放容量，不能让一个修复中的单元
  占住整组容量、阻止后续就绪内容。不会因为一本书只有一个大章节而启动整本书数量的并发任务。
- 每组成员保留各自 canonical 译文及 fill 修复循环。GroupRequests 显式等待本组
  成员进入模型请求或完成，再 flush 就绪 conversation；既有 token/输出预算及
  64 单元上限仍可提前切批。该收集不占 provider 槽位，也不等待兄弟单元完成 repair。
  没有内容组的独立调用仍使用原 10ms 稀疏调用期限；它不再是正文分组的依据。
- 组包预算为 `max(4096, max_group_score * 4)`。公共 system 指令只传输和计费一次；
  每个 conversation 只携带自己的消息历史、文本和 XML 模板，按实际编码后的 JSON
  预留同等输出 token，再计入共享指令和外层 envelope 的开销。canonical 验收说明
  属于共享 system 指令，公式引用表仍属于各自单元。预算约束新增聚合。
  单个已由 mapper 拆分的超预算单元沿用单请求，不为凑预算截断语义内容。
- 每个 operation 的 request window 与 LLM executor 继续限制真实网络并发。
  自定义 PCEX transformer 保留原有 job-window 契约。页眉页脚的就绪工作也采用
  显式收集，metadata、anchored content 与正文保持独立进度，避免辅助字段成为交付屏障。
- mapper 在调度前将内容组拆成独立 canonical 单元，按来源顺序增量归还 mapping；
  不再等整组完成后一次性返回。前面已验收的段落不等待后面段落的 repair。
  同一原子 owner 或包围它的 owner 尚未完整验收时，仍按原有通知边界等待。
- 完整的独立单元继续通过原来的提交和通知链交付。聚合的 HTTP 响应完成后，各单元可以
  独立验收；失败单元的 repair 不将成功单元重新入队。原有来源顺序、owner 原子性及回调
  规则保持，章节文件在它的单元全部结算后收尾。
- 组包本身没有修复循环。fill 缺少某个外层 ID 时，仅该单元收到空响应并消耗原有一次
  fill 尝试；重复/未知身份或非法 envelope 拒绝该响应。首阶段缺失身份按翻译失败处理。
  网络失败仍由 LLMRuntime 的原有传输预算处理，各单元随后沿用自己的 ErrorPolicy。
- 单个等待者取消不影响同批其他等待者；最后一个等待者离开时取消并等待底层清理。
  共享请求自行拥有缓存上下文，避免首个等待者提前退出导致缓存残留。
- 每个耗尽的 fill loop 日志包含它参与的真实批次请求/响应及之后自己的修复请求；成功
  loop 的临时日志照常删除。实际模型用量每批只计一次，任一成果被采用则该批记为有效；
  全部未采用则记为浪费。不把一次请求虚构成 N 次或估算各单元 token 用量。
  外层 envelope 不完整时撤销整批缓存写入，但当前轮仍保留可验证的独立结果。
- 不同 operation、HTTP headers、usage collector、阶段和修复调度参数不能混批。

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
几何结构的往返。开发期离线运行观察到 12–14 次；这是正确模拟响应的调用计数，
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
