# 端到端测试 (E2E Tests)

本目录包含了用于验证代理网关（LiteLLM 和 APIM）部署与功能的测试脚本，以确保所有模型请求均能被正确路由。

## 测试文件说明

- `test_all_deployments.py`: 统一的端到端网络测试脚本，同时兼容发往 LiteLLM 或 APIM 代理的大型语言模型验证。验证能力涵盖文本、图片和视频模型的可用性，同时覆盖了对标准 OpenAI API 格式以及 Azure OpenAI API 格式路由的请求兼容。
- `test_codex_cache_affinity.py`: 使用真实 Codex CLI 在不同输入 Token 档位创建并续接会话，统计 Azure Prompt Cache 命中率，并可通过 LiteLLM Spend Logs 验证两轮请求命中同一后端 `model_id`。

## 环境要求

运行测试脚本前，请确保代理服务已经在运行中。这些脚本只依赖 Python 的标准库（如 `urllib`, `json`, `argparse` 等），因此无需安装任何外部依赖（如 `requests` 或 `openai`）。

## 使用指南

### 1. 测试基础网关代理 (LiteLLM / APIM)

需要提供针对不同代理的部署配置 `azure-openai.json` 文件的路径、代理服务的基础 URL (Base URL) 以及访问该代理服务的对应 API Key。

```bash
python test_all_deployments.py \
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

## Codex 缓存命中与会话亲和测试

### 前置条件

- Codex `config.toml` 中已经配置 `model_providers.litellm`；
- Provider 使用 `wire_api = "responses"`；
- LiteLLM 已配置 `responses_api_deployment_check`、`deployment_affinity` 和 `session_affinity`；
- 测试模型已经由 LiteLLM 暴露；
- 本机已安装 Codex CLI；
- 使用 `--verify-backend-affinity` 时，当前 `kubectl` context 必须能访问 LiteLLM AKS。

先在当前 PowerShell 会话设置 LiteLLM Virtual Key：

```powershell
$env:LITELLM_API_KEY = Read-Host "LiteLLM Virtual Key"
```

### 为什么不能只测两轮

在连续任务中，第 $n$ 轮请求包含此前历史和本轮新增输入。即使可复用前缀全部命中，两轮时 `cached_input_tokens / input_tokens` 也通常只有约 50%；随着轮数增加，最终轮才会逐步接近 90%。因此脚本默认模拟 10 轮完整工程任务，依次覆盖需求、风险、架构、安全、可靠性、成本、测试、发布和最终评审。

### 第一次：记录 simple-shuffle 基线

先临时关闭 `optional_pre_call_checks` 并保持 `routing_strategy: simple-shuffle`。`--routing-mode` 只标记结果和选择验收规则，**不会自动修改生产 Router 配置**。

配置生效并完成 Pod rollout 后运行：

```powershell
python .\tests\test_codex_cache_affinity.py `
  --model gpt-5.6-terra `
  --sizes 1024,4096,8192 `
  --rounds 10 `
  --routing-mode simple-shuffle `
  --verify-backend-affinity `
  --output-json "$env:TEMP\codex-cache-simple-shuffle.json"
```

simple-shuffle 模式只采集基线，不会因为缓存率低或后端发生切换而返回失败。

### 第二次：启用 affinity 并比较

重新启用：

```yaml
optional_pre_call_checks:
  - responses_api_deployment_check
  - deployment_affinity
  - session_affinity
deployment_affinity_ttl_seconds: 3600
```

完成 rollout 后运行：

```powershell
python .\tests\test_codex_cache_affinity.py `
  --model gpt-5.6-terra `
  --sizes 1024,4096,8192 `
  --rounds 10 `
  --routing-mode affinity `
  --min-final-hit-rate 0.85 `
  --min-prefix-continuity 0.90 `
  --verify-backend-affinity `
  --compare-json "$env:TEMP\codex-cache-simple-shuffle.json" `
  --output-json "$env:TEMP\codex-cache-affinity.json"
```

每个 `--sizes` 档位都会执行：

1. 创建全新的 Codex Session；
2. 首轮注入该规模的稳定项目背景；
3. 使用同一个 Session 连续完成 10 个不同的工程子任务；
4. 从每轮 `turn.completed` 读取真实 `input_tokens` 和 `cached_input_tokens`；
5. 输出逐轮缓存曲线、最终轮、稳态、全任务加权和前缀连续率；
6. 查询 LiteLLM Spend Logs，以稳定 `cache_key` 关联全部轮次；
7. 统计唯一 `model_id` 数和相邻轮次的后端切换次数；
8. 与 simple-shuffle JSON 按相同 Payload 档位计算指标差值。

`--sizes` 是用于构造 Payload 的近似 Token 数。最终统计始终使用 Codex 返回的实际 Token 数，因为 Codex 的系统指令、工具定义和会话历史也会计入输入。

affinity 实测的逐轮曲线示例：

```text
round  input_tokens  cached_tokens  total_rate  previous_prefix
1      32444         16079          49.56%      WARM
2      49436         32438          65.62%      99.98%
3      66623         49427          74.19%      99.98%
...
10     192880        174109         90.27%      99.98%

payload  rounds  final_rate  steady_rate  aggregate  continuity  backends  transitions  status
512      10      90.27%      89.30%       85.15%     100.00%     1         0            PASS
```

字段解释：

| 字段 | 含义 |
|---|---|
| `payload` | 请求构造的近似 Payload Token 档位 |
| `final_rate` | 最后一轮 `cached_input_tokens / input_tokens`；10 轮 affinity 预期接近 90% |
| `steady_rate` | 最后三轮命中率的算术平均 |
| `aggregate` | 除首轮外，所有轮次缓存 Token / 所有轮次输入 Token |
| `continuity` | `cached_input_tokens` 至少覆盖上一轮输入 90% 的轮次比例 |
| `backends` | 同一 `cache_key` 在 Spend Logs 中出现的唯一 `model_id` 数 |
| `transitions` | 按时间排列后，相邻轮次 `model_id` 变化的次数 |
| `status` | affinity 模式下最终轮、前缀连续率和单后端校验是否通过 |

LiteLLM `1.95.0` 的 WebSocket Spend Logs 可能显示 `prompt_tokens=0`、`api_base` 为空，因此脚本不会使用这两个字段关联请求。Codex 会为同一会话发送稳定 `prompt_cache_key`，LiteLLM 将其记录为相同 `cache_key`；脚本以该字段关联完整任务的全部轮次，并用 `model_id` 序列判断后端亲和。

测试完成后清理环境变量：

```powershell
Remove-Item Env:LITELLM_API_KEY -ErrorAction SilentlyContinue
```