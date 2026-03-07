# Azure OpenAI 负载均衡与配额突破方案

本项目提供了一个完整的解决方案，用于突破单一 Azure 订阅中 Azure OpenAI 服务的配额限制。通过实现具备负载均衡功能的反向代理，将请求分发到多个 Azure OpenAI 资源上，并能够稳健地处理 **429 (Too Many Requests)**、**500** 和 **503** 等常见错误。

## 🌟 核心特性

- **突破配额**: 聚合多个 Azure OpenAI 资源/订阅的吞吐量，突破单实例瓶颈。
- **自动容错**: 针对 429、500和503 错误提供零延迟的自动重试、切换路由逻辑。
- **模型路由兼容**: 深度适配普通 Chat 请求、DALL-E 图像端点，并对 Sora 等特定模型的非标准路径规则进行精准处理。
- **协议兼容**: 同时原生支持 Azure OpenAI 格式 API、OpenAI 兼容格式 API 以及最新的低延迟 Response API 路由。
- **多部署选项**:
  1. **Azure API Management (APIM)**: 全托管、云原生的 Azure 解决方案。
  2. **LiteLLM on AKS**: 高性价比的开源代理方案。

## 📂 项目结构

- **`requirements.txt`**: 根目录统一项目的依赖文件。
- **`tests/`**: 统一的端到端（E2E）测试框架文件夹。

### 1. Azure API Management (APIM)
位于 [`APIM/`](./APIM/) 目录。该方案使用 Azure 原生 API Management 服务智能管理流量。

### 2. LiteLLM on AKS
位于 [`LiteLLM/`](./LiteLLM/) 目录。该方案将开源 OpenAI 代理库部署在 AKS 上。

## ⚙️ 快速上手

**1. 安装全局依赖:**
```bash
pip install -r requirements.txt
```

**2. 查阅模块部署指南:**
- 分别进入 `APIM/` 或 `LiteLLM/` 目录执行自动化搭建。

**3. 执行全局验证测试:**
- 当部署完成后，使用 `tests/test_all_deployments_all.py` 执行验证脚本。
