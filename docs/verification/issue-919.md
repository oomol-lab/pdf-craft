# OCR 请求 PNG 大小限制回归

关联：https://github.com/oomol/pdf-craft-service/issues/266 。该 Issue 还包含内容安全等其他
OCR 错误；本次只验证图片大小，不代表整个服务端 Issue 已解决。

## 验证边界

以预设 DPI 首次渲染，只有实际 PNG 超限才降低 DPI；下一次分辨率按实测大小反馈修正，
目标字节数取上限的 97%。每张输入图片最多生成 5 次，含首次；DPI/像素长边严格下降，
达到 1 或耗尽次数仍超限时明确失败。正常图片不因预估压缩率提前降 DPI。

标准 PDF 后端每次从原 PDF 重新渲染。自定义异步 PDF handler 已提供并物化的原始 raster，
则每次从该原始 raster 缩放，不从前一次缩小后的图片继续缩放。脚注第二轮和供应商的
请求图片尺寸变换也检查实际编码大小；各派生图片有同样的有界预算，检测框回映到页面
来源像素空间。最终文件在调用 URL resolver（包括用户的上传 resolver）前再检查。

## 可重复执行的测试

普通回归不读取私有文件、不访问 OCR 服务：

```sh
.venv/bin/python -m unittest tests.test_ocr_image_size -v
```

覆盖实测字节反馈、边界相等与刚超限、正常图片、五次预算与最小分辨率、非法限制、
取消与编码异常资源释放、本地 OCR、异步 handler 物化 raster、vendor 两种调度路径、
脚注第二轮缩放/坐标，以及 Unlimited 请求缩放/坐标。独立的传输端检查测试保证超限文件
不能进入 resolver。无需模型、CUDA 或真实网络调用。

真实线上材料按用户确认的位置保留在仓库外：

```text
/Users/taozeyu/Downloads/pdfcraft-investigations/issue-266-image-size/original.pdf
/Users/taozeyu/Downloads/pdfcraft-investigations/issue-266-image-size/oversized-page-1.png
```

PDF SHA-256 为
`b9bf53eb0080c6258bb8e2f785f31b33ecbd8fcfb1649656e9e20f8e2c477458`；
归档 PNG SHA-256 为
`193f9c2342c3515004bede38b9dda7c0bfa2a54ae8620118279143612030595f`。
测试会校验这两份材料，缺失或不符即失败。普通测试默认跳过此私有材料门禁；显式执行：

```sh
PDF_CRAFT_IMAGE_SIZE_REGRESSION_PDF=/Users/taozeyu/Downloads/pdfcraft-investigations/issue-266-image-size/original.pdf \
PDF_CRAFT_IMAGE_SIZE_REGRESSION_OUTPUT=pdf-craft-output/issue-919-gate \
.venv/bin/python -m unittest tests.test_ocr_image_size_real -v
```

真实门禁选择全新临时 OCR workspace，只提取第 1 页。保留真实 Poppler 渲染、Pillow PNG
编码、反馈循环、OCR 响应解析及页 XML 写入；仅替换网络响应，并由异步 resolver 独立检查
上传前实际文件。模拟 OCR 返回固定归一化 bbox，以检查页面记录与来源 bbox 的一致性，
不代表真实 OCR 识别精度验收，也不产生付费请求。输出每轮 DPI、尺寸、大小和 resolver
实测结果；可选输出目录保存 trace JSON 与缩略对比图，均不进入提交。

## 2026-10-31 实测

环境：Python 3.11.15、Poppler 26.10.0 默认后端、Pillow 12.0.0 PNG 编码。上限为十进制
10,000,000 字节，预设 300 DPI。真实 vendor 页面准备及本地请求拦截结果：

| 生成次数 | DPI | 像素尺寸 | PNG 字节数 |
| --- | --- | --- | --- |
| 1 | 300 | 4450 × 6849 | 23,835,790 |
| 2 | 191 | 2833 × 4361 | 12,722,896 |
| 3 | 166 | 2462 × 3790 | 10,355,694 |
| 4 | 160 | 2373 × 3653 | 9,803,120 |

resolver 只收到最终的 9,803,120 字节图片，前面三张超限图片均未提交。
最终 PNG 可解码；页尺寸记录为 2373 × 3653，模拟 OCR bbox 回映与之匹配，页面完成且
没有失败降级。原始/最终缩略图人工对比未见裁边或页面内容缺失，主要文字与图形仍可辨认。

归档线上第 1 页 PNG 为 23,575,243 字节；不同编码环境不要求重现相同字节。
历史线上调用未传大小限制；旧的首次 DPI 预估在本文件启用 10,000,000 字节上限时约为
140 DPI、8,023,135 字节。因此本例证明的是预设质量下超限及新反馈机制有效，不能据此
声称旧版启用该参数后本页仍超限。仅检查最终小于 10 MB 无法区分新旧机制，门禁还必须
检查首次 300 DPI、实际超限触发和有界重试。

云服务仍需显式设置该参数并接入引擎修复；本仓库未修改服务端配置或部署。

实现阶段验证：新增 17 项普通大小回归通过；真实材料门禁另行显式执行通过；
`python test.py` 全量 1353 项通过（跳过 1 项默认关闭的私有材料门禁）。
`pyright --pythonpath .venv/bin/python pdf_craft tests` 零错误；
`pylint pdf_craft tests` 通过。以上是实现者验证记录，独立 Review 仍须自行复验。
