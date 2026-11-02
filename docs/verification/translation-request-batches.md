# 跨章节翻译请求打包

章节决定内容归属，StructuredTranslation 决定 canonical 验收单元，
RequestBatcher 决定模型请求。这三个边界互相独立。PCEX 正文、脚注、资产字段与原生
EPUB 共用 XMLTranslator 的两个请求入口：首阶段翻译和 fill。

组包不修改 PCEX 持久化结构、版本、coverage 或来源身份，也不更换 EPUB 的原节点与 CFI。
每个单元先得到独立 canonical 译文，再执行原有结构、Unicode、空白和公式验收。
同一批请求里的局部 XML ID、math-ref key 使用各自 conversation 命名空间；外层 JSON
显式携带请求内唯一 ID，输出允许重排。禁止按空行或正文拆分结果。

## 有界与失败语义

- 每批最多 64 个 conversation，最多等待 10ms。达到单元数或 token 预算立即发出；
  稀疏输入不依赖 EOF，也不在模型 executor 内等待其他输入。
- 组包预算为 `max(4096, max_group_score * 4)`。公共 system 指令只传输和计费一次；
  每个 conversation 只携带自己的消息历史、文本和 XML 模板，按实际编码后的 JSON
  预留同等输出 token，再计入共享指令和外层 envelope 的开销。canonical 验收说明
  属于共享 system 指令，公式引用表仍属于各自单元。预算约束新增聚合。
  单个已由 mapper 拆分的超预算单元沿用单请求，不为凑预算截断语义内容。
- 内置 PCEX 调度及 XML mapper 使用独立的有界就绪窗口 `max(window, 64)`。
  默认 `window=1` 也能收集多个单元、跨章节组包；网络并发仍由调用者原来的共享
  LLM executor 限制，并用 operation 共享的 request window 保留调用者的请求并发
  上限；等待请求槽位前已经完成组包，不为每组创建模型 executor。自定义 PCEX transformer 保留原有
  job-window 语义。不预载全书、不等待下一章、不创建整书单元 future 列表。
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

## 无网络实验入口

在仓库根目录运行：

```sh
.venv/bin/python -m unittest tests.test_request_batch -v
```

测试只替换 LLMRuntime 的 HTTP transport，保留真正的 tokenizer、executor、请求分发、
canonical fill、公式还原和结果提交。`TRANSLATION_BATCH_COUNTS` 输出按真正 transport
调用统计的 JSON，包含首阶段和 fill，既不计逻辑任务，也不计缓存命中。
默认窗口的 PCEX 公共 API 和异步 EPUB 对照使用输入就绪屏障，让六个单元同时
到达生产 RequestBatcher；不替换组包策略或 10ms 截止线。这样既能检测 window=1
阻止后续内容入场的错误，也避免把机器 IO 速度误当作组包能力。稀疏输入的实时交付
另有不使用屏障的测试。最初的 Narrative 对照仍单独使用可控 flush。结果为：

| 布局 | 首阶段请求 | fill 请求 | 采用段落 |
| --- | ---: | ---: | ---: |
| 1 章 6 段 | 1 | 1 | 6 |
| 6 章各 1 段 | 1 | 1 | 6 |

重构前两种 PCEX 布局均为 6 + 6 次。此表是离线协议实验，不能代表真实模型翻译质量或
整书节省比例。实际组包受 token 大小、输入到达时间、调度窗口与修复次数影响。
`PCEX_SCHEDULE_BATCH_COUNTS` 另外统计完整 PCEX 调度及译文层导出的实际请求（包括
元数据）；该固定夹具没有可翻译元数据，使用默认窗口和上述输入就绪屏障。真实运行的计数
仍会随输入 IO/10ms 就绪截止线变化。
该测试同时检查 6 条正文 coverage 均为 translated，不用文件存在冒充成功。

48 个短段落放一章或 48 个异步根的回归，均验证首阶段 1 次加 fill 1 次请求，
并逐段检查实际译文和来源归属；同一案例在首版组包中需要 6 + 12 次请求。
该对照使用 max_group_score=10000；默认预算 2600 的六段案例仍为 1 + 1。
另有确定性屏障测试将第二段的修复挂起，确认第一段已在挂起期间交付，
再释放并检查第二段恰好交付一次。

同一测试模块还覆盖异步跨根 EPUB、真实 PCEX 译文层导出、局部 fill 错误/耗尽证据、
重复局部公式身份、长短单元/批次上限、输入暂停时交付和取消清理。

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

该门禁仍逐片段检查原始节点端点，不再把旧的 253 个请求分组当作业务身份。
私有现场不入库；真实模型效果、首段延迟及付费请求数由 Origin 后续亲自实验。
