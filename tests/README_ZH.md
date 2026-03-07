# 端到端测试 (E2E Tests)

本目录包含了用于验证代理网关（LiteLLM 和 APIM）部署与功能的测试脚本，以确保所有模型请求均能被正确路由。

## 测试文件说明

- `test_all_deployments_all.py`: 统一的端到端网络测试脚本，同时兼容发往 LiteLLM 或 APIM 代理的大型语言模型验证。验证能力涵盖文本、图片和视频模型的可用性，同时覆盖了对标准 OpenAI API 格式以及 Azure OpenAI API 格式路由的请求兼容。

## 环境要求

运行测试脚本前，请确保代理服务已经在运行中。这些脚本只依赖 Python 的标准库（如 `urllib`, `json`, `argparse` 等），因此无需安装任何外部依赖（如 `requests` 或 `openai`）。

## 使用指南

### 1. 测试基础网关代理 (LiteLLM / APIM)

需要提供针对不同代理的部署配置 `azure-openai.json` 文件的路径、代理服务的基础 URL (Base URL) 以及访问该代理服务的对应 API Key。

```bash
python test_all_deployments_all.py \
  --config ../LiteLLM/azure-openai.json \
  --base-url http://<GATEWAY_EXTERNAL_IP_OR_DOMAIN>:<PORT> \
  --api-key <YOUR_GATEWAY_API_KEY>
```

**可选参数:**
- `--config`: 用于指定对应的模型部署配置映射文件（如 LiteLLM 或 APIM 目录下的 `azure-openai.json`）的路径。
- `--base-url`: 代理对外暴露的基础请求端点。
- `--api-key`: 发送请求所需的代理认证 API Key。（注：脚本内置了同时发送 `Authorization: Bearer` 和 `api-key:` Header 的功能，天然兼容 LiteLLM 或 APIM 的鉴权机制）
- `--prompt`: （可选）文本模型测试时的自定义提示词。默认为: "请只回复: ok"。
- `--image-prompt`: （可选）图片生成模型验证时的自定义提示词。

## 测试覆盖特性

1. **文本模型 (Chat API)**: 在标准的 OpenAI 端点 (`/v1/chat/completions`) 以及 Azure OpenAI 端点 (`/openai/deployments/...`) 路径下双向发送请求。
2. **图片模型 (Image API)**: 验证标准 OpenAI 的画图端点 (`/v1/images/generations`) 和对应的 Azure OpenAI 路由。
3. **视频模型 (例如 Sora)**: 通过向网关代理的模型注册表列出接口 (`/v1/models`) 请求，验证注册内容是否存在。