# LiteLLM 用户预算与模型权限配置指南

本文说明如何在已部署的 LiteLLM Proxy 中，为不同用户设置预算、限制可用模型，并让用户通过 OpenAI 兼容 API 选择模型。

## 1. 核心概念

LiteLLM 的访问控制通常由以下对象组成：

```text
User
  └── Virtual Key
          ├── 可用模型列表
          ├── max_budget
          └── budget_duration

Team
  ├── 多个用户或 Virtual Key
  ├── 团队共享预算
  └── 团队模型权限
```

- **Internal User**：内部用户，例如员工、开发者或团队成员。
- **Team**：按部门、项目或应用组织用户和 Key。
- **Virtual Key**：真正用于调用 LiteLLM API 的访问凭据。
- **max_budget**：预算上限，通常按美元计。
- **budget_duration**：预算周期，例如 `1d`、`30d`。
- **models**：该 User、Team 或 Key 允许调用的模型白名单。

对于个人额度，建议每个用户使用一个独立 Virtual Key；对于部门或项目额度，建议使用 Team 共享预算。

## 2. 当前部署可用模型

模型名称来自 `azure-openai.json` 的 `deployment_list`。当前示例中的模型是：

```text
gpt-5.6-luna
gpt-5.4-pro
gpt-5.4
gpt-4.1
```

调用方在请求体的 `model` 字段中使用这些名称。LiteLLM 会把它们映射到对应的 Azure OpenAI deployment。

## 3. 通过管理界面配置

打开 LiteLLM Admin UI：

```text
http://<LOAD_BALANCER_IP>:4000/ui
```

使用管理员账号登录后，推荐按以下顺序配置：

### 3.1 创建用户

进入 `Internal Users` 或 `Users`，创建用户，例如：

```text
User ID: alice
Email: alice@example.com
```

### 3.2 创建 Team

进入 `Teams`，按部门、项目或应用创建 Team，例如：

```text
Team Alias: Data Science
Max Budget: 200
Budget Duration: 30d
```

Team 预算会汇总该 Team 下的 Key 使用量。

### 3.3 配置模型权限

在 Team 或 Virtual Key 的模型权限中选择允许的模型。例如普通用户可以只允许：

```text
gpt-4.1
gpt-5.4
```

高级用户可以允许：

```text
gpt-4.1
gpt-5.4
gpt-5.4-pro
gpt-5.6-luna
```

### 3.4 生成 Virtual Key

在 `Virtual Keys` 中创建 Key，并关联 User 或 Team，然后设置：

```text
User: Alice
Models: gpt-4.1, gpt-5.4
Max Budget: 20
Budget Duration: 30d
```

将生成的 Key 交给 Alice。普通用户不应使用管理员 Master Key。

## 4. 通过 Management API 配置

管理员 Key 仅用于管理 API，不要分发给普通用户。以下命令使用 PowerShell 示例：

```powershell
$env:BASE_URL = "http://<LOAD_BALANCER_IP>:4000"
$env:MASTER_KEY = "<LITELLM_MASTER_KEY>"

$headers = @{
    Authorization = "Bearer $env:MASTER_KEY"
    "Content-Type" = "application/json"
}
```

### 4.1 创建 Team

```powershell
$body = @{
    team_alias = "Data Science"
    max_budget = 200
    budget_duration = "30d"
    models = @(
        "gpt-4.1",
        "gpt-5.4"
    )
} | ConvertTo-Json

$team = Invoke-RestMethod `
    -Uri "$env:BASE_URL/team/new" `
    -Method Post `
    -Headers $headers `
    -Body $body

$team
```

保存返回值中的 `team_id`，后续生成 Team Key 时使用。

### 4.2 创建用户专属 Key

```powershell
$body = @{
    user_id = "alice"
    key_alias = "alice-key"
    models = @(
        "gpt-4.1",
        "gpt-5.4"
    )
    max_budget = 20
    budget_duration = "30d"
} | ConvertTo-Json

$key = Invoke-RestMethod `
    -Uri "$env:BASE_URL/key/generate" `
    -Method Post `
    -Headers $headers `
    -Body $body

$key
```

返回结果中的 `key` 是 Alice 调用 API 时使用的凭据。

### 4.3 创建 Team 下的 Key

```powershell
$body = @{
    team_id = "<TEAM_ID>"
    user_id = "alice"
    key_alias = "alice-team-key"
    models = @(
        "gpt-4.1",
        "gpt-5.4"
    )
} | ConvertTo-Json

$key = Invoke-RestMethod `
    -Uri "$env:BASE_URL/key/generate" `
    -Method Post `
    -Headers $headers `
    -Body $body

$key
```

这种方式下，多个 Key 的消费会计入同一个 Team 预算。若需要个人独立预算，使用不关联 Team 的用户专属 Key。

## 5. 用户如何选择模型

用户在请求体中填写 `model`：

```powershell
$userHeaders = @{
    Authorization = "Bearer <USER_VIRTUAL_KEY>"
    "Content-Type" = "application/json"
}

$body = @{
    model = "gpt-5.4"
    messages = @(
        @{
            role = "user"
            content = "Hello"
        }
    )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
    -Uri "http://<LOAD_BALANCER_IP>:4000/v1/chat/completions" `
    -Method Post `
    -Headers $userHeaders `
    -Body $body
```

请求链路为：

```text
请求中的 model
  ↓
Virtual Key 的 models 白名单
  ↓
LiteLLM model_list
  ↓
Azure OpenAI deployment
```

如果用户请求了不在 Key 白名单中的模型，LiteLLM 应拒绝该请求。

## 6. 推荐的初始权限方案

| 用户类型 | 可用模型 | 月预算 |
| --- | --- | ---: |
| 普通用户 | `gpt-4.1`, `gpt-5.4` | $20 |
| 高级用户 | `gpt-4.1`, `gpt-5.4`, `gpt-5.4-pro` | $100 |
| 研究/管理员 | 全部模型 | $500 |

## 7. 安全注意事项

- 不要把 `LITELLM_MASTER_KEY` 分发给普通用户。
- 当前服务使用公网 LoadBalancer，生产环境应增加网络访问限制、TLS 和更强的身份认证。
- 不要把真实 Master Key、Virtual Key、数据库密码提交到 Git。
- 修改 `LITELLM_MASTER_KEY` 后，需要重新运行部署脚本，让 Kubernetes Secret 和 LiteLLM Pod 更新。
- 预算和用量统计依赖 PostgreSQL；生产环境建议使用高可用数据库和备份策略。