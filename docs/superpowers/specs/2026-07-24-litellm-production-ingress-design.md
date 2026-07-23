# LiteLLM AKS 生产入口改造设计

## 目标

将现有直接暴露 `LoadBalancer:4000` 的 LiteLLM AKS 部署改为由 AKS Application Routing 托管 NGINX Ingress 提供唯一公网入口。公网客户端通过自有域名使用 HTTPS 访问 LiteLLM；LiteLLM 和 PostgreSQL 均不再具有独立公网入口。

本方案以较低改造和运行成本为优先，不部署 Azure Front Door、Application Gateway 或 WAF。

## 范围

### 包含

- 通过部署脚本启用 AKS Application Routing（外部托管 NGINX）及 Key Vault 集成。
- 通过环境变量显式配置网关域名、Key Vault 和证书名称。
- 创建 TLS Ingress，并将 HTTP 请求永久跳转至 HTTPS。
- 将 LiteLLM Kubernetes Service 改为 `ClusterIP`。
- 要求非默认的 LiteLLM Master Key 和 PostgreSQL 密码。
- 为 LiteLLM 与 PostgreSQL 增加健康探针、资源 requests/limits 与容器安全上下文。
- 为入口提供可选 CIDR 白名单配置。
- 提供生产架构和阿里云域名绑定操作文档。

### 不包含

- 自动购买域名、申请/导入证书或修改阿里云 DNS。
- 部署 Azure Front Door、Application Gateway、WAF 或 DDoS Protection Standard。
- 将单副本的集群内 PostgreSQL 自动迁移为 Azure Database for PostgreSQL。
- 在本次改造中部署 Gateway API。托管 NGINX 当前可用于该低成本方案，但需在 2026 年 11 月前规划迁移；这是 Microsoft 对 Application Routing NGINX 的支持截止时间。

## 目标架构

```text
客户端 / Codex / API 调用方
  |
  | HTTPS:443  litellm.example.com
  v
阿里云 DNS A 记录
  |
  v
AKS Application Routing 托管 NGINX LoadBalancer
  |  TLS 终止；证书来自 Azure Key Vault；可选 CIDR 白名单
  v
LiteLLM Ingress (namespace: litellm)
  v
LiteLLM ClusterIP Service:4000
  v
LiteLLM Pod -- Managed Identity --> Azure OpenAI
  |
  v
PostgreSQL ClusterIP Service:5432
```

LiteLLM Service 不拥有 External IP。Ingress 控制器的 LoadBalancer 是唯一公网 IP；阿里云 DNS 的 A 记录指向该 IP。

## 配置接口

部署前必须设置：

| 环境变量 | 用途 |
| --- | --- |
| `LITELLM_HOSTNAME` | 公网 FQDN，例如 `litellm.example.com`。 |
| `LITELLM_KEYVAULT_NAME` | 存放 TLS 证书的 Azure Key Vault 名称。 |
| `LITELLM_KEYVAULT_CERT_NAME` | Key Vault 中的 TLS 证书名称。 |
| `LITELLM_MASTER_KEY` | 非默认、强随机 LiteLLM 管理密钥。 |
| `PG_PASSWORD` | 非默认、强随机 PostgreSQL 密码。 |

可选设置：

| 环境变量 | 用途 |
| --- | --- |
| `LITELLM_ALLOWED_CIDRS` | 逗号分隔的允许来源 CIDR。未设置时不施加网络白名单，仍由 TLS 和 LiteLLM Virtual Key 保护。 |
| `LITELLM_REPLICAS` | LiteLLM 副本数，默认 2。 |

脚本通过 Azure CLI 启用/更新 Application Routing，并将 Key Vault 附加给该加载项；Azure 会授予加载项托管身份读取证书所需的权限。脚本从 Key Vault 查询证书 URI 并去掉版本号，供 Ingress 轮换时持续使用。

## Kubernetes 资源行为

- `litellm-mi-proxy`：`ClusterIP`，端口 4000；只作为 Ingress 后端。
- `litellm-ingress`：使用类 `webapprouting.kubernetes.azure.com`；主机名等于 `LITELLM_HOSTNAME`；使用 Key Vault TLS 证书；301/308 跳转 HTTP 到 HTTPS。
- LiteLLM / PostgreSQL：设置 CPU 与内存 requests/limits，并设置 liveness/readiness probes。
- LiteLLM 容器：禁止特权提升、丢弃 Linux capabilities，并以非 root 用户运行；若镜像不兼容则部署应显式失败，而不是静默降级。

## 验证与失败处理

部署脚本在创建 Ingress 后等待其 Address，再以 `https://<hostname>` 执行既有 OpenAI 与 Azure OpenAI 格式 smoke test。

脚本在下列情况下失败且不打印敏感值：缺少必填生产配置、仍使用仓库默认密钥、Application Routing/Key Vault 命令失败、证书不存在、Ingress 没有在超时内取得公网地址、HTTPS smoke test 失败。

DNS 记录由操作者在阿里云创建，因此在首次部署和 DNS 生效之间，脚本允许通过 `LITELLM_SKIP_PUBLIC_SMOKE_TEST=true` 跳过公网 smoke test。Ingress Address、域名和验证命令会始终输出。

## 文档交付

- `LiteLLM/PRODUCTION_ARCHITECTURE_ZH.md`：架构、流量与身份边界、密钥管理、运维要求、明确的非 HA PostgreSQL 限制与迁移建议。
- `LiteLLM/CUSTOM_DOMAIN_SETUP_ZH.md`：阿里云域名准备、证书导入 Key Vault、环境变量、部署、A 记录、验证、故障排查和回滚。

## 测试

为脚本中新增的配置校验、ClusterIP Service、Ingress 生成和 URL 选择添加单元测试。现有订阅选择测试必须保持通过。运行 Python 单元测试，并执行部署脚本的 `--help` 检查。
