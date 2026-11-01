# 跨章节翻译请求打包

章节决定内容归属，StructuredTranslation 决定 canonical 验收单元，
RequestBatcher 决定模型请求。这三个边界互相独立。PCEX 正文、脚注、资产字段与原生
EPUB 共用 XMLTranslator 的两个请求入口：首阶段翻译和 fill。

组包不修改 PCEX 持久化结构、版本、coverage 或来源身份，也不更换 EPUB 的原节点与 CFI。
每个单元先得到独立 canonical 译文，再执行原有结构、Unicode、空白和公式验收。
同一批请求里的局部 XML ID、math-ref key 使用各自 conversation 命名空间；外层 JSON
显式携带请求内唯一 ID，输出允许重排。禁止按空行或正文拆分结果。

## 有界与失败语义

- 每批最多 8 个 conversation，最多等待 10ms。达到单元数或 token 预算立即发出；
  稀疏输入不依赖 EOF，也不在模型 executor 内等待其他输入。
- 组包预算为 `max(4096, max_group_score * 4)`。对每个 conversation 统计实际编码后的
  messages/JSON，并预留同等输出 token，再计入外层指令；预算约束新增聚合。
  单个已由 mapper 拆分的超预算单元沿用单请求，不为凑预算截断语义内容。
- mapper 的每个有界组最多同时推进 8 个独立 canonical 单元，外部章节/内容调度窗口保持
  原有作用；网络并发仍由原来的共享 LLM executor 限制，不为每组创建模型 executor。
  不预载全书、不等待下一章、不创建整书单元 future 列表。
- 完整的独立单元继续通过原来的提交和通知链交付。聚合的 HTTP 响应完成后，各单元可以
  独立验收；失败单元的 repair 不将成功单元重新入队。原有来源顺序、owner 原子性及回调
  规则保持，不承诺绕过这些规则交付。
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
固定 6 个短段落的结果为：

| 布局 | 首阶段请求 | fill 请求 | 采用段落 |
| --- | ---: | ---: | ---: |
| 1 章 6 段 | 1 | 1 | 6 |
| 6 章各 1 段 | 1 | 1 | 6 |

重构前两种 PCEX 布局均为 6 + 6 次。此表是离线协议实验，不能代表真实模型翻译质量或
整书节省比例。实际组包受 token 大小、输入到达时间、调度窗口与修复次数影响。
`PCEX_SCHEDULE_BATCH_COUNTS` 另外统计完整 PCEX 调度及译文层导出的实际请求（包括
元数据）；它不是上表的直接 Narrative 对照，计数会随输入 IO/10ms 就绪截止线变化。
该测试同时检查 6 条正文 coverage 均为 translated，不用文件存在冒充成功。

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
