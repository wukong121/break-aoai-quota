# LiteLLM Virtual Key 配置 Codex 教程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一份已验证的中文 Word 教程，指导用户在 Windows、macOS 与 Linux 上为 Codex CLI 和桌面应用配置 LiteLLM Virtual Key。

**Architecture:** 使用单个 `docx-js` 脚本生成独立的 A4 Word 文档。正文以统一原理为主线，并提供三套平台命令、CLI/桌面应用说明、端到端示例和排障表；随后由 Office 验证脚本检查 DOCX 包结构。

**Tech Stack:** Node.js、`docx`（docx-js）、Python Office 验证脚本。

## Global Constraints

- 文档语言为简体中文，覆盖 Windows、macOS、Linux、Codex CLI 与 Codex 桌面应用。
- 所有密钥和 URL 均使用明确的占位符或不可用演示值。
- 纸张为 A4，表格使用 DXA 固定宽度，列表使用 Word 编号定义。
- 最终 `.docx` 必须通过 `validate.py` 验证。

---

### Task 1: 编写 Word 生成脚本

**Files:**
- Create: `generate_litellm_codex_virtual_key_tutorial.js`
- Produces: 生成 `LiteLLM-Virtual-Key-配置-Codex-教程.docx` 的 Node.js 脚本。

- [ ] **Step 1: 建立文档内容与样式**

在脚本中创建 A4 `Document`、中文默认字体、标题样式、页眉页脚和编号配置。正文包含：概览、准备信息、安全规则、通用配置、三个操作系统章节、端到端示例、验证/排障/回退。

- [ ] **Step 2: 写入平台命令与 Codex 配置示例**

为 Windows 添加 PowerShell 命令；为 macOS/Linux 添加 zsh/bash 命令。每个平台同时说明 CLI 如何从终端环境读取配置，以及桌面应用如何从启动环境/全局配置读取配置。

- [ ] **Step 3: 写入端到端示例和排障表**

示例统一使用：

```text
LITELLM_PROXY_URL=https://litellm.example.com/v1
VIRTUAL_KEY=sk-litellm-example-replace-me
MODEL_NAME=gpt-4.1-mini
```

排障表涵盖 401、404、模型不可用、URL `/v1`、环境变量和回退步骤。

- [ ] **Step 4: 运行生成脚本**

Run: `node .\\generate_litellm_codex_virtual_key_tutorial.js`

Expected: 在仓库根目录生成 `LiteLLM-Virtual-Key-配置-Codex-教程.docx`。

### Task 2: 验证交付文档

**Files:**
- Verify: `LiteLLM-Virtual-Key-配置-Codex-教程.docx`

- [ ] **Step 1: 验证 Office 文档结构**

Run: `python C:\\Users\\wangpeter\\.codex\\plugins\\cache\\anthropic-agent-skills\\document-skills\\local\\skills\\docx\\scripts\\office\\validate.py .\\LiteLLM-Virtual-Key-配置-Codex-教程.docx`

Expected: 验证通过，未报告 DOCX XML 或包结构错误。

- [ ] **Step 2: 检查交付文件**

Run: `Get-Item .\\LiteLLM-Virtual-Key-配置-Codex-教程.docx | Select-Object Name,Length`

Expected: 文件存在且长度大于零。

- [ ] **Step 3: 提交教程产物**

Run: `git add generate_litellm_codex_virtual_key_tutorial.js LiteLLM-Virtual-Key-配置-Codex-教程.docx`

Expected: 仅教程脚本和教程文档被暂存；不包含现有未跟踪文件。
