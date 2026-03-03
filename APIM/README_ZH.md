# Azure OpenAI 负载均衡解决方案 (APIM 智能代理)

本项目提供了一个全自动化脚本，用于部署 **Azure API Management (APIM)** 作为 Azure OpenAI 服务的智能负载均衡器。该方案不仅可以聚合多个区域的 Azure OpenAI 资源实现高可用，还提供双协议支持（Azure 原生 SDK 与 OpenAI 官方 SDK 兼容）。

## 🚀 核心优势

*   **智能负载均衡**：自动将流量分配到多个 Azure OpenAI 后端实例（默认支持轮询 Round-Robin）。
*   **高可用与韧性设计**：内置重试逻辑，当遇到 `429 Too Many Requests` 或 `5xx Server Errors` 时，API 管理服务会自动切换到下一个可用后端重试，极大减少业务中断。
*   **双模式协议支持 (Dual API)**：
    *   **Azure 原生 API (`/openai`)**：如 `https://<apim>.azure-api.net/openai`，完美兼容 Azure OpenAI Python/Node/C# SDK。
    *   **OpenAI 兼容 API (`/v1`)**：如 `https://<apim>.azure-api.net/v1`，直接兼容 OpenAI 官方库，无需修改代码即可迁移应用。
    *   **新增 Responses API 支持**：同时在 `/openai/responses` 和 `/v1/responses` 提供低延迟的 Responses API 访问（已内置版本重写逻辑）。
*   **无密钥安全性 (Managed Identity)**：在 APIM 与后端 OpenAI 服务之间使用 **托管身份 (Managed Identity)** 通信，彻底消除在代码或 APIM 中明文存储 Backend Key 的安全风险。
*   **多模型与长连接支持**：经验证支持 GPT-4o, Reasoning 模型 (o1-preview/gpt-5.2) 以及 DALL-E 3 (图片生成，已优化超时设置)。
*   **跨订阅资源聚合**：支持配置不同 Azure 订阅下的 OpenAI 资源，实现跨订阅的高可用集群。

---

## 🛠️ 前置要求

*   **Python 3.10 及以上**
*   **Azure CLI 登录** (`az login`)：确保拥有足够权限创建资源。
*   **准备好的 Azure OpenAI 资源**：建议在不同区域（如美东 + 瑞典中部）创建同名模型部署，以获得最佳可用性。

## 📂 项目结构

*   `deploy_aoai_nlb.py`: 核心部署脚本 (Python)，负责创建 APIM、API 定义以及策略配置。
*   `azure-openai.json`: 配置文件，定义了后端资源列表、APIM 名称以及所需部署的模型。
*   `test_apim.py`: 统一的测试工具，支持两种模式验证。
*   `requirements.txt`: 项目 Python 依赖。

---

## ⚙️ 配置说明

1.  **安装依赖**：
    ```bash
    pip install -r requirements.txt
    ```

2.  **编辑配置文件 (`azure-openai.json`)**：
    指定你的 APIM 名称、目标资源组、后端 OpenAI 实例列表。注意：`reverse_mode` 参数已废弃，脚本现默认同时部署两种模式。

    ```json
    {
        "apim_name": "my-unique-apim-name",
        "apim_resource_group": "my-resource-group",
        "region": "eastus2",
        "azure-openai-list": [
            {
                "name": "instance-eastus",
                "endpoint": "https://instance-eastus.openai.azure.com/",
                "resource_group": "rg-eastus"
            },
            {
                "name": "instance-europe-sub2",
                "endpoint": "https://instance-europe.openai.azure.com/",
                "resource_group": "rg-europe",
                "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  // 可选：指定资源所在的订阅 ID（如不填默认为当前）
            }
        ],
        "deployment_list": [
            { "model": "gpt-4o", "deployment_name": "gpt-4o" },
            { "model": "gpt-image-1", "deployment_name": "dall-e-3" }
        ]
    }
    ```

---

## 📦 部署步骤

直接运行 Python 部署脚本。该脚本会自动检查并创建 APIM 服务（如果不存在），配置后端池，应用负载均衡策略，并启用托管身份。

```bash
python deploy_aoai_nlb.py
```

*注意：首次创建 APIM 服务可能需要 30-45 分钟。更新 API 配置通常只需几秒钟。*

**脚本执行细节：**
1.  创建 APIM 服务 (Standard V2 SKU)。
2.  为 APIM 启用用户分配托管身份 (User-Assigned Managed Identity)，并自动为后端 OpenAI 资源分配 `Cognitive Services OpenAI User` 角色（支持跨订阅）。
3.  **注意**：角色分配现在使用确定性 GUID 来防止冲突并支持幂等性。
4.  创建两个 API 入口：
    *   `/openai` (Azure Mode + Responses API)
    *   `/v1` (OpenAI Compatible Mode)

---

## 🧪 测试与验证

部署完成后，脚本会输出 **Gateway URL**。请前往 Azure Portal (API Management -> Subscriptions) 获取 **Subscription Key**。

使用提供的 `test_apim.py` 脚本验证部署是否成功。

### 1. 测试 Azure 原生模式
模拟 Azure OpenAI SDK，测试 `/openai/deployments/...` 路径。

```bash
python test_apim.py \
  --mode azure \
  --url "https://<your-apim-name>.azure-api.net/" \
  --key "<subscription-key>"
```

### 2. 测试 OpenAI 兼容模式
模拟标准 OpenAI SDK，测试 `/v1/chat/completions` 路径（无需 Azure Endpoint，仅需 Base URL）。

```bash
python test_apim.py \
  --mode openai \
  --url "https://<your-apim-name>.azure-api.net/" \
  --key "<subscription-key>"
```

---

## 📝 策略与技术细节

本方案部署了高级 XML 策略 (Policy)，主要功能包括：

1.  **后端轮询 (Backend Rotation)**：使用随机算法选择健康的后端。
2.  **故障转移 (Retry Strategy)**：
    *   当后端返回 `429` (限流) 或 `5xx` (服务端错误) 时自动重试。
    *   采用指数退避算法 (Exponential Backoff)。
3.  **身份认证 (Transformation)**：移除客户端传入的 API Key，自动注入 APIM 的 Managed Identity Token (`Authorization: Bearer <token>`) 与后端通信。
4.  **路由重写 (OpenAI Mode)**：动态将 `/v1/chat/completions` 请求映射为 Azure 特有的 `/openai/deployments/{model}/chat/completions?api-version=...` 格式。
