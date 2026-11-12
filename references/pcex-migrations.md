# PCEX Migration 边界

**约束范围：** PCEX 版本、历史字段转换与业务消费边界。当前格式是 v8；包版本和 PCEX 格式版本不是同一件事。

历史规则的心智负担必须集中在 `pdf_craft/document/migrations.py`，不得分散到翻译、渲染、内容流或其他业务适配器。归档/旧 workspace 在私有物化副本上校验、迁移，再交付当前格式；原输入不修改。业务不探测版本，也不根据缺失字段猜测历史语义。当前格式的 `image.content` 是非展示、非翻译的源数据，图外说明只能来自 `title/caption`；缺少 caption 不允许回退到 content。

v1/v2 的章节 codec 仅供输入迁移/历史格式校验显式调用，业务解码默认严格。导出器只打包已规范化的当前格式，不另写一套升级逻辑。

修改字段结构或语义时：

1. 升级格式版本，在 migration 模块增加明确的历史版本转换；不得仅改文档声明，或维持原版本并在消费方容错。
2. 按源结构确定转换，同步迁移所有已有译文层。保留 flow/slot 身份、coverage、源与译文对应、公式及混合内容；不能给不同层分别猜测字段含义。
3. 新生产者直接生成当前契约。旧格式歧义只在 migration 中处理和记录；不要让业务调用 migration helper。
4. 测试旧版本读取后直接翻译/渲染、已有译文层选择、重新导出、迁移幂等性和原文件不变；也测试当前版本的相似字段形态不会触发旧语义。
5. 真实错误的历史结构必须先拒绝，不能让迁移掩盖结构损坏。用真实现场验收最终输出，不能只断言文件存在。

v4→v5 将没有显式 title/caption 的旧图片 content 移到 caption，并按源结构同步移动译文层字段。已有显式字段时不合并 content。这个历史规则不判断图像像素，也不属于 v5 消费者的行为。任何后续旧数据规则都只允许进入 migration 模块。

v6 将来源槽与逻辑片段身份分开；历史源及全部译文层在 migration 中同步增加身份，coverage 的历史整框键保持有效。不在业务消费方补旧字段。来源范围/原文证据未知时不伪造。


v7 允许 furniture 的普通 position 和无 association 的 section 文本为空。coverage=translated 的空值是已采用的空译文；PDF 写回执行只擦除，不恢复原文字。身份、几何、引用和关联结构仍严格保留。v6→v7 不改写源和既有译文层的文字、身份或 coverage，只升级格式声明；历史格式仍先按原约束校验。


v8 在 narrative coverage 下增加 formula：chapter_id 与 path（body:INDEX 或
note:PAGE-ORDER:INDEX）定位独立公式，state 为 translated/preserved。章节内容 schema
不变；旧版本译文中的独立公式是冻结来源，migration 为根级及所有译文层显式补 preserved，
不猜测已翻译，不修改公式内容或原始归档。运行时只消费当前 coverage，不按旧名字兼容。
