# LiteLLM Managed Identity AKS Deployment

本目录包含一个 Python 自动化脚本，用于将 LiteLLM Proxy 部署到 Azure Kubernetes Service (AKS)，并配置 Managed Identity (MI) 以实现对 Azure OpenAI 资源的无密钥访问。

## ✨ 功能特性

- **一键自动化**：自动创建/更新 Azure 资源（Resource Group, Managed Identity, AKS）。
- **安全集成**：
  - 自动将 User-Assigned Managed Identity 绑定到 AKS 节点的 VMSS。
  - 自动为 Managed Identity 在目标 Azure OpenAI 资源上分配 `Cognitive Services OpenAI User` 角色（支持跨订阅）。
- **内置数据库**：自动在 AKS 中部署 PostgreSQL 用于 LiteLLM 数据存储。
- **自动配置**：根据配置文件（如 `azure-openai.json`）自动生成 LiteLLM 配置文件和 K8s 资源（Secret, ConfigMap）。
- **双模式支持**：同时支持 OpenAI 原生格式 (`/v1/chat/completions`) 和 Azure OpenAI 格式 (`/openai/deployments/...`)。
- **自检功能**：部署完成后自动运行冒烟测试，验证 API 连通性。

## 📋 前置条件

1. **环境依赖**：
   - Python 3.8+
   - Azure CLI (`az`)
   - `kubectl` (建议预装，脚本运行过程中主要使用 `kubernetes` Python 库)

2. **Azure 登录**：
   确保你已通过 Azure CLI 登录并选择了正确的订阅：
   ```bash
   az login
   az account set --subscription <YOUR_SUBSCRIPTION_ID>
   ```

3. **依赖安装**：
   ```bash
   pip3 install -r requirements.txt
   ```

## ⚙️ 配置文件 (azure-openai.json)

在运行脚本前，请准备 `azure-openai.json` 文件。该文件定义了目标 Azure OpenAI 资源和部署模型列表。

**示例结构：**

```json
{
  "region": "swedencentral",
  "apim_resource_group": "litellm-resources-rg",
  "managed_identity": "litellm-identity-prod",
  "azure-openai-list": [
    {
      "name": "my-aoai-resource-1",
      "resource_group": "ai-resources-rg",
      "subscription_id": "00000000-0000-0000-0000-000000000000" // 可选，若跨订阅必须指定
    },
    {
      "name": "my-aoai-resource-2",
      "resource_group": "another-rg"
    }
  ],
  "deployment_list": [
    {
      "model": "gpt-4",
      "deployment_name": "gpt-4-turbo"
    },
    {
      "model": "gpt-35-turbo",
      "deployment_name": "gpt-35-turbo"
    }
  ]
}
```

## 🚀 使用方法

运行 Python 脚本即可开始部署。默认会读取当前目录下的 `azure-openai.json`。

```bash
python3 deploy_mi_aks_litellm.py
```

或者指定配置文件路径：

```bash
python3 deploy_mi_aks_litellm.py /path/to/my-config.json
```

### 环境变量配置

你可以通过环境变量覆盖默认配置：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MI_NAME` | `litellm-managed-identity` | Managed Identity 名称 (若 json 中未指定) |
| `AKS_NAME` | `litellm-mi-aks` | AKS 集群名称 |
| `AKS_NODE_COUNT` | `1` | AKS 节点数量 |
| `AKS_VM_SIZE` | `Standard_B2als_v2` | AKS 节点 VM 规格 |
| `AKS_NAMESPACE` | `litellm` | Kubernetes 命名空间 |
| `LITELLM_IMAGE` | `micl/litellm:mi-fix-image-gen` | LiteLLM 镜像地址 |
| `LITELLM_MASTER_KEY` | `sk-local-mi-test-key` | LiteLLM Master Key (用于管理和 API 调用) |
| `PG_STORAGE` | `1Gi` | PostgreSQL PVC 大小 |
| `RUN_SMOKE_TEST` | `true` | 部署后是否运行冒烟测试 |

## 🔍 验证部署

脚本执行成功后，会输出服务的访问信息。你可以手动执行以下命令检查集群状态：

```bash
# 获取 Pods 和 Services
kubectl -n litellm get pods,svc

# 查看 LiteLLM 日志
kubectl -n litellm logs deploy/litellm-mi-proxy --tail=100 -f
```

### 冒烟测试

脚本会自动对部署的服务执行冒烟测试，验证 `/v1/responses` (OpenAI style) 和 `/openai/v1/responses` (Azure style) 接口是否可用。

如果你想手动运行全量测试，可以使用提供的测试脚本：

```bash
python3 test_all_deployments_responses.py
```

## 🏗️ 架构说明

脚本会在 AKS 中创建以下资源：

1. **Namespace**: `litellm`
2. **ConfigMap**: `litellm-config` (包含生成的 `litellm.config.yaml`)
3. **Secret**: `litellm-env` (包含 `AZURE_CLIENT_ID`, `DATABASE_URL`, `LITELLM_MASTER_KEY` 等敏感信息)
4. **PostgreSQL**:
   - Deployment: `postgres`
   - Service: `postgres`
   - PVC: `pg-data`
5. **LiteLLM Proxy**:
   - Deployment: `litellm-mi-proxy`
   - Service: `litellm-mi-proxy` (Type: LoadBalancer)
