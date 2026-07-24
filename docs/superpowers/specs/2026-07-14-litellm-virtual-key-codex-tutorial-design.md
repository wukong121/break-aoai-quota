# LiteLLM Virtual Key 配置 Codex 教程：设计说明

## 目标

交付一份中文 Word 教程，帮助终端用户将 LiteLLM Proxy 的 Virtual Key 配置为 Codex 的 API 凭据，并在 Windows、macOS 与 Linux 上完成验证。教程必须同时覆盖 Codex CLI 与 Codex 桌面应用。

## 受众与边界

受众是已拥有 LiteLLM Proxy 地址、Virtual Key 和可访问模型的普通开发者。教程不负责部署 LiteLLM、创建 Virtual Key 或配置代理路由；这些内容作为前置条件说明。

## 文档结构

1. 概览：说明 Codex 通过 OpenAI 兼容接口访问 LiteLLM，Virtual Key 是用户凭据。
2. 准备信息与安全规则：列出 Proxy URL、Virtual Key、模型名，禁止将密钥写入仓库、聊天记录或截图。
3. 统一配置原理：使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `~/.codex/config.toml` 的模型提供方配置。
4. Windows：PowerShell 临时与持久环境变量，配置文件路径，CLI 与桌面应用的启动/重启步骤。
5. macOS：zsh 环境变量与配置文件路径，CLI 与桌面应用的启动/重启步骤。
6. Linux：bash/zsh 环境变量与配置文件路径，CLI 与桌面应用的启动/重启步骤。
7. 端到端示例：以 `https://litellm.example.com/v1`、`sk-litellm-example-replace-me` 和 `gpt-4.1-mini` 演示从配置到 `codex` 发起任务及预期现象。
8. 验证、排障与回退：401、404、模型不可用、URL 末尾 `/v1`、环境变量未生效、恢复默认 OpenAI 配置。

## 取舍

采用一本完整教程而不是按平台拆分，保证一致性。命令使用明显占位符，端到端示例的密钥为不可用示例值，防止读者将文档中的内容误作真实密钥。

## 验收标准

- 三个平台各有可复制命令和文件路径。
- CLI 与桌面应用都有明确配置/重启说明。
- 包含一段完整示例、验证命令与预期结果。
- 包含密钥安全和故障排查内容。
- 生成的 `.docx` 通过 Office 文档结构验证。
