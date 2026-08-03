# LiteLLM on Azure Kubernetes Service (AKS)

在 AKS 群集上自动化部署 [LiteLLM](https://github.com/BerriAI/litellm)，以实现 Azure OpenAI 负载均衡、故障重试、用量统计和按用户/团队的预算控制。

## 📂 结构

- `deploy_mi_aks_litellm.py`: AKS、Managed Identity、PostgreSQL 和 LiteLLM Proxy 部署脚本。
- `azure-openai.json`: 可提交到仓库的 Azure OpenAI 配置模板。
- `azure-openai.loc.json`: 本机实际部署配置（已被 `.gitignore` 忽略，不要提交）。
- `USER_BUDGET_AND_MODEL_ACCESS_ZH.md`: 用户、Team、Virtual Key、预算和模型权限配置指南。
- FOUNDRY_MODEL_SYNC_ZH.md: Foundry 新增模型 deployment 后同步到 LiteLLM 的操作指南。
- `litellm.config.yaml`: 部署脚本根据 JSON 自动生成的 LiteLLM 配置，不建议手工修改。

*注：测试与依赖项已整合至项目根目录 (`../tests/` 与 `../requirements.txt`)。*

## ⚙️ 使用说明

### 1. 准备本地配置

```powershell
# 如果当前位于 LiteLLM 目录，先返回项目根目录安装依赖
cd ..
python -m pip install -r .\requirements.txt

# 登录 Azure 并明确选择创建 AKS 所使用的订阅
az login
$env:AZURE_SUBSCRIPTION_ID = "<AKS 所在订阅 ID>"
az account set --subscription $env:AZURE_SUBSCRIPTION_ID

# 返回 LiteLLM 目录，首次使用时从模板创建本地配置
cd .\LiteLLM
if (-not (Test-Path .\azure-openai.loc.json)) {
  Copy-Item .\azure-openai.json .\azure-openai.loc.json
}
```

编辑 `azure-openai.loc.json`，填写实际的区域、资源组、Azure OpenAI 资源和模型 deployment。脚本默认优先读取该文件；不存在时才读取模板 `azure-openai.json`，因此不需要在两者之间手工复制更新。

脚本会在 `azure-openai.loc.json` 的 `apim_resource_group` 中创建或复用 Resource Group、Managed Identity 和 AKS。LiteLLM 方案中该字段是历史命名，实际表示 AKS/Managed Identity 所在的资源组，并不是 APIM 资源组。脚本还会根据 `azure-openai-list[].subscription_id`，为跨订阅 Azure OpenAI Resource 分配 Managed Identity 权限。

### 2. 设置部署环境变量

以下是绑定域名并启用 HTTPS 的推荐生产配置。`LETSENCRYPT_EMAIL` 是 Let's Encrypt 的 ACME 账户和证书通知邮箱，不是自签名证书邮箱。

```powershell
# Azure 和 AKS
$env:AZURE_SUBSCRIPTION_ID = "<AKS 所在订阅 ID>"
$env:AKS_NAME = "litellm-mi-aks"
$env:AKS_VM_SIZE = "Standard_D2s_v3"
$env:AKS_NODE_COUNT = "1"

# 必须改成 AKS 能拉取的镜像；私有 ACR 还需提前授予 AKS AcrPull 权限
$env:LITELLM_IMAGE = "<acr-name>.azurecr.io/litellm:<tag>"

# 生产环境建议固定注入；至少 24 个字符，不要提交到 Git
$env:LITELLM_MASTER_KEY = "sk-<强随机密钥>"
$env:PG_PASSWORD = "<强随机数据库密码>"

# 设置域名会启用 ingress-nginx、cert-manager 和 HTTPS
$env:LITELLM_HOSTNAME = "litellm.example.com"
$env:LETSENCRYPT_EMAIL = "admin@example.com"
```

> 若未设置 `LITELLM_MASTER_KEY`，脚本默认自动生成强随机 Key，并在部署结束时显示一次。重新部署时也会再次生成新 Key，使旧 Master Key 失效，因此生产环境应始终从 Key Vault 或其他密钥管理系统注入固定值。

### 3. 执行部署

```powershell
python .\deploy_mi_aks_litellm.py
```

也可以显式传入其他配置文件：

```powershell
python .\deploy_mi_aks_litellm.py .\customer-a.loc.json
```

### 支持的环境变量

环境变量必须在启动 Python 进程前设置。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `AZURE_SUBSCRIPTION_ID` | 当前 Azure CLI 订阅 | 创建 AKS、Managed Identity 和资源组的订阅；建议显式设置 |
| `MI_NAME` | `litellm-managed-identity` | User Assigned Managed Identity 名称 |
| `AKS_NAME` | `litellm-mi-aks` | AKS 名称 |
| `AKS_NODE_COUNT` | `1` | AKS 节点数 |
| `AKS_VM_SIZE` | `Standard_D2s_v3` | AKS 节点规格 |
| `AKS_NAMESPACE` | `litellm` | Kubernetes namespace |
| `LITELLM_IMAGE` | `micl/litellm:mi-fix-image-gen` | LiteLLM 容器镜像；客户部署应显式指定可拉取镜像 |
| `LITELLM_MASTER_KEY` | 空 | 管理员 Key，至少 24 个字符；为空时按下一项决定是否生成 |
| `AUTO_GENERATE_MASTER_KEY` | `true` | Master Key 为空时是否自动生成；设为 `false` 可强制要求外部注入 |
| `LITELLM_STARTUP_WAIT_SECONDS` | `180` | 等待 Prisma migration 和 Uvicorn 启动的秒数 |
| `LITELLM_HOSTNAME` | 空 | 域名；非空时启用 Ingress + HTTPS，否则使用公网 `LoadBalancer:4000` |
| `LETSENCRYPT_EMAIL` | 空 | 启用域名时必填的 Let's Encrypt 邮箱 |
| `INGRESS_PROXY_BODY_SIZE` | `100m` | NGINX 最大请求体 |
| `INGRESS_PROXY_BUFFERING` | `off` | NGINX 响应缓冲设置，流式输出建议保持关闭 |
| `INGRESS_PROXY_READ_TIMEOUT` | `600` | NGINX 上游读取超时（秒） |
| `INGRESS_PROXY_SEND_TIMEOUT` | `600` | NGINX 上游发送超时（秒） |
| `AZURE_SCOPE` | `https://cognitiveservices.azure.com/.default` | Managed Identity 获取令牌的 scope |
| `AZURE_API_VERSION` | 空 | Smoke test 使用的 Azure API version 查询参数 |
| `OPENAI_ROLE_NAME` | `Cognitive Services OpenAI User` | 分配给 Managed Identity 的角色 |
| `RUN_SMOKE_TEST` | `true` | LoadBalancer 模式下是否执行部署后测试；Ingress 模式会跳过 |
| `PG_USER` | `litellm` | PostgreSQL 用户名 |
| `PG_PASSWORD` | `litellm-local-dev` | PostgreSQL 密码；生产环境必须覆盖 |
| `PG_DB` | `litellm` | PostgreSQL 数据库名 |
| `PG_STORAGE` | `1Gi` | PostgreSQL PVC 容量 |

默认 AKS 规格为：

```text
区域：由 azure-openai.loc.json 的 region 决定
节点数：1
VM：Standard_D2s_v3
```

如果目标订阅或区域不支持该规格，可以临时指定：

```powershell
$env:AKS_VM_SIZE = "Standard_B2ms"
```

## 🔐 用户预算和模型权限

部署完成后，打开：

```text
http://<AKS LoadBalancer IP>:4000/ui
```

然后通过 `Internal Users`、`Teams` 和 `Virtual Keys` 配置：

- 每个用户的预算和预算周期；
- Team 共享预算；
- Virtual Key 可访问的模型白名单；
- 用户和 Team 的用量统计。

详细步骤见 [`USER_BUDGET_AND_MODEL_ACCESS_ZH.md`](./USER_BUDGET_AND_MODEL_ACCESS_ZH.md)。

## 🧪 验证部署

实际测试脚本文件是 `../tests/test_all_deployments.py`：

```powershell
python ..\tests\test_all_deployments.py `
  --config .\azure-openai.loc.json `
  --base-url "http://<AKS LoadBalancer IP>:4000" `
  --api-key "<LiteLLM Virtual Key>" `
  --prompt ok
```

测试会验证 OpenAI 风格和 Azure OpenAI 风格的 Chat 路由。使用 Windows 默认控制台时，建议传入 ASCII prompt，避免 `cp1252` 无法输出中文造成测试脚本提前退出。

## ⚠️ 注意事项

- 不要把管理员 Master Key 分发给普通用户；应为每个用户创建 Virtual Key。
- 未设置 `LITELLM_HOSTNAME` 时使用公网 `LoadBalancer:4000`；生产环境建议配置域名、TLS、网络访问限制和强随机 Key。
- PostgreSQL 当前是 AKS 内单副本部署，适合验证和轻量场景；生产环境建议使用高可用数据库和备份。

## 🌐 绑定自有域名并启用 HTTPS

想通过 `https://litellm.你的域名.com` 访问（而不是 `http://<IP>:4000`），请按上面的生产配置同时设置 `LITELLM_HOSTNAME` 和 `LETSENCRYPT_EMAIL`。脚本会自动配置 ingress-nginx + cert-manager（Let's Encrypt 证书）。

```powershell
$env:LITELLM_HOSTNAME = "litellm.example.com"   # 触发 Ingress 模式
$env:LETSENCRYPT_EMAIL = "you@example.com"       # Let's Encrypt 证书邮箱（必填）
$env:LITELLM_IMAGE = "<acr-name>.azurecr.io/litellm:<tag>"
$env:LITELLM_MASTER_KEY = "sk-<强随机密钥>"
python .\deploy_mi_aks_litellm.py
```

- 前置条件：本机已安装 Helm 和 kubectl。
- 脚本结束会打印 ingress 公网 IP，你到阿里云 DNS 加一条 A 记录指向它，DNS 生效后证书自动签发。
- 不设 `LITELLM_HOSTNAME` 时保持原有 `LoadBalancer:4000` 行为。

完整步骤（买域名、DNS、证书、验证、排查、回滚）见 [`CUSTOM_DOMAIN_SETUP_ZH.md`](./CUSTOM_DOMAIN_SETUP_ZH.md)。