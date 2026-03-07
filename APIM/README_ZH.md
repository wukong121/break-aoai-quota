# Azure OpenAI 负载均衡方案 (APIM)

本方案自动化部署 **Azure API Management (APIM)** 作为 Azure OpenAI 的智能负载均衡器。

## 🚀 核心优势
* **智能负载均衡及容错**: 针对 `429`、`500` 和 `503` 错误提供精准重试轮询。
* **双协议支持**: 同时支持 Native Azure 模式和 OpenAI 兼容模式。
* **模型分离支持**: 精准区分模型能力，针对推理模型、Sora多模态模型单独路由，排除不兼容的 OpenAI 模式端点映射。
* **Responses API 支持**: 支持针对超低延迟端点的代理改写。
* **无密钥安全**: 采用 Managed Identity 托管身份通信。

## 📂 结构
* `deploy_mi_apim.py`: 主部署脚本。
* `azure-openai.json`: 后端与模型配置。

*注：测试与依赖项已整合至项目根目录 (`../tests/` 与 `../requirements.txt`)。*

## ⚙️ 使用说明
```bash
# 1. 安装根目录依赖
pip install -r ../requirements.txt

# 2. 配置 azure-openai.json
# 3. 部署
python deploy_mi_apim.py

# 4. 测试部署
python ../tests/test_all_deployments_all.py --config azure-openai.json --base-url "https://<your-apim>.azure-api.net" --api-key "<subscription-key>"
```
