# 提取语义定稿边界验收（#891）

## 边界变化

来源 OCR 是证据；传统分析和 LLM 精修是内部候选。印刷目录页识别先行，但不分配
章节 ID。统一定稿出口从已采用内容派生目录与 furniture，再校验 PCEX；正式正文／
脚注回调只交付最终内容一次。删除、降为正文、合并标题不再要求撤回精修。

派生 TOC 不再按文件存在与否复用。候选、恢复结果及下游页投影均为本次临时数据，
不能覆盖原 OCR。旧目录 ID 修补模块已删除。metadata 的原始证据语义不变。
内容回调会更晚开始；计算并发和实时进度保留，待交付数据落磁盘。

## 离线回归

`tests/test_extraction_commit.py` 的首四项在修改包代码前执行：3 failures、1 error。
分别证明标题消失导致目录失配、候选提前交付、furniture 误用旧占用、旧目录缓存被信任。
修复后通过，并补充脚注通道标题晋升、恢复图片占用、脚注唯一有序交付。
既有晚期恢复测试还发现目录工作单元重复启动，已修复并通过其生命周期断言。

最终 `python test.py`：**1215 tests，全部通过**（含事件并发、磁盘队列和取消测试）。
Pyright：0 errors / 0 warnings；改动 Python 文件 pylint：10.00/10。
原先要求“撤回精修保留旧目录 ID”的测试改为要求真正采用精修且不降级；并未撤掉
严格错误策略、最终结构校验或真实存储／回调错误传播检查。

## 真实调用

本工作树 `pdf-craft-output/issue891-evidence/` 保存日志、客户回归脚本及过滤后的 smoke 配置。
运行时使用本地配置与 OOMOL provider，不提交密钥、客户资料或生成产物。
Paid 日志中的 commit 是运行开始时的基线 HEAD；实际执行的是本工作树修改后的源码。
最后仅清理了未被严格成功路径调用的旧目录恢复辅助代码；清理后完整离线检查通过。

| 验收 | 结果与证据 |
| --- | --- |
| Morals，原页 32–49 | 全新 OCR 目录、四路并发，18 页均完成，146.91 秒；66 个正文、1 个脚注回调与最终 PCEX 一致 |
| Strategic，原页 7–24 | 全新 OCR 目录、四路并发，18 页均完成，108.41 秒；56 个正文回调与最终 PCEX 一致 |
| citation_footnotes | 三页真实 OCR、精修、翻译、PDF 回填完成；几何遮罩全覆盖且没有多擦区域，错误策略记录为空 |
| table/formula vendor matrix | ZIP 完整，22 个正文段落全部 translated；表格 translated，纯图片 preserved；Markdown 含表格和公式 |
| fill_formula_loop | 连续三轮，每轮三个用例全部通过；未靠 fallback 或缺失译文通过 |

两个客户用例还验证：每页内部消费恰好一次、全部页完成、操作完成一次、无错误降级。
裁片中心只是历史推测范围，不能声称覆盖了准确的原线上竞态页。此次新 Strategic 响应
保留了 DILEMMAS OF INTERACTION；强制标题消失的确定性回归由离线测试覆盖。

证据目录：

- `pdf-craft-output/issue891-customers-01/summary.json`
- `pdf-craft-output/issue891-citation-01/{summary.json,citation-checks.json,previews/}`
- `pdf-craft-output/issue891-table-01/`
- `pdf-craft-output/issue891-formula-01/acceptance.json`

人工查看了 citation 三页原图／译图：未见原报告中的脚注重复覆盖。
这不是对全部排版质量的保证：第三页公式在 PDF 回填预览中显示不完整，历史 #886
验收产物也有同类问题；本次 PCEX 保留了完整 display-formula 内容和资源，公式 Loop
及 Markdown 公式验收通过。该 PDF 渲染现象未在本任务中修改，盲审应独立核对。

## 发版留待单独处理

未修改版本号、未新增 changelog，也未修改已发布的 `docs/changelog/v2.6.5.md`。
本页及功能文档记录本次变化，供之后整理发布说明。首次内容回调时机改变及派生缓存
重算应在下一版发布说明中特别提示。
