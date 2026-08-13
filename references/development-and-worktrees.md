# 开发与 Worktree

**约束范围：** setup、验证、VGE worktree 行为、清理和发布命令。**不约束：** 包架构或转换内部逻辑。**何时阅读：** 选择命令、编辑项目配置或修改开发文档时。

## 环境

使用 Python `>=3.11,<3.14`；默认开发版本是 Python 3.11。本仓库使用 Poetry 2.x；配置 `poetry config virtualenvs.in-project true` 时，虚拟环境应位于当前 worktree 内。

普通开发安装依赖：

```bash
poetry config virtualenvs.in-project true
poetry install --with dev
```

只有当任务需要覆盖 import 行为或真实 OCR 行为时，才按当前环境单独安装 `torch` 和 `torchvision`。

## 默认验证

优先选择能跨越改动边界的最小命令集：

```bash
poetry run pyright pdf_craft tests
poetry run pylint pdf_craft tests
poetry run python test.py
```

`poetry build` 用于验证打包。`scripts/` 中的完整转换脚本是手动检查，可能需要 Poppler、PyTorch、模型缓存和 CUDA。

## VGE Worktree 行为

本项目没有长期运行的开发服务，因此 `.conductor/settings.toml` 不应定义 `scripts.run`，除非未来项目新增 watcher 或 server。VGE setup 只应安装依赖。Cleanup 应清理 worktree 本地虚拟环境、测试缓存、构建产物和转换输出。

生成的转换输出应留在当前 worktree 的 `analysing/` 下。模型缓存可能很大；只有在有意进行本地 OCR 工作时才使用 `models-cache/`，不要提交它，也不要假设其中已有内容。

## 人类阅读文档

面向人类贡献者的开发说明位于 `docs/DEVELOPMENT.md` 和 `docs/DEVELOPMENT_zh-CN.md`。README 文件应聚焦库使用者。Agent 面向的路由和项目约束保留在 `AGENTS.md` 和 `references/` 中。

## 发布

发布说明位于 `docs/RELEASE.md`。除非任务明确要求发布或升级依赖，否则普通 worktree 支持改造不应修改包版本、依赖 pin 或发布元数据。
