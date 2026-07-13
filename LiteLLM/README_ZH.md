# LiteLLM on Azure Kubernetes Service (AKS)

在 AKS 群集上自动化部署 [LiteLLM](https://github.com/BerriAI/litellm)，以实现 Azure OpenAI 负载均衡、故障重试、用量统计和按用户/团队的预算控制。

## 📂 结构

- `deploy_mi_aks_litellm.py`: AKS、Managed Identity、PostgreSQL 和 LiteLLM Proxy 部署脚本。
- `azure-openai.json`: Azure OpenAI 资源和 deployment 配置。
- `USER_BUDGET_AND_MODEL_ACCESS_ZH.md`: 用户、Team、Virtual Key、预算和模型权限配置指南。
- FOUNDRY_MODEL_SYNC_ZH.md: Foundry 新增模型 deployment 后同步到 LiteLLM 的操作指南。
- `litellm.config.yaml`: 部署脚本根据 JSON 自动生成的 LiteLLM 配置，不建议手工修改。

*注：测试与依赖项已整合至项目根目录 (`../tests/` 与 `../requirements.txt`)。*

## ⚙️ 使用说明

```powershell
# 1. 在项目根目录安装依赖
cd ..
python -m pip install -r .\requirements.txt

# 2. 登录 Azure 并明确指定创建 AKS 所使用的订阅
az login
$env:AZURE_SUBSCRIPTION_ID = "<AKS 所在订阅 ID>"

# 3. 返回 LiteLLM 目录并部署
cd .\LiteLLM
python .\deploy_mi_aks_litellm.py
```

脚本会在 `azure-openai.json` 的 `apim_resource_group` 中创建或复用 Resource Group、Managed Identity 和 AKS。LiteLLM 方案中该字段是历史命名，实际表示 AKS/Managed Identity 所在的资源组，并不是 APIM 资源组。

默认 AKS 规格为：

```text
区域：eastus2
节点数：1
VM：Standard_B2s
```

如果目标订阅或区域不支持该规格，可以临时指定：

```powershell
$env:AKS_VM_SIZE = "Standard_B2ms"
```

脚本还会通过 `azure-openai-list[].subscription_id` 为跨订阅 Azure OpenAI Resource 分配 Managed Identity 权限。

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
  --config .\azure-openai.json `
  --base-url "http://<AKS LoadBalancer IP>:4000" `
  --api-key "<LiteLLM Virtual Key>" `
  --prompt ok
```

测试会验证 OpenAI 风格和 Azure OpenAI 风格的 Chat 路由。使用 Windows 默认控制台时，建议传入 ASCII prompt，避免 `cp1252` 无法输出中文造成测试脚本提前退出。

## ⚠️ 注意事项

- 不要把管理员 Master Key 分发给普通用户；应为每个用户创建 Virtual Key。
- 当前部署使用公网 LoadBalancer，生产环境应增加 TLS、网络访问限制和强随机 Key。
- PostgreSQL 当前是 AKS 内单副本部署，适合验证和轻量场景；生产环境建议使用高可用数据库和备份。