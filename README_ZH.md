# Azure OpenAI 负载均衡与配额突破方案

本项目提供了一套综合解决方案，通过反向代理和负载均衡技术，突破单个 Azure 订阅对 Azure OpenAI 服务的配额限制。它能有效处理高并发场景下的 **429 (Too Many Requests)**、**500** 和 **503** 错误，确保业务稳定运行。

## 🌟 核心功能

- **突破配额**：聚合多个 Azure OpenAI 资源/订阅的吞吐量，实现真正的线性扩展。
- **智能重试**：针对 429、500、503 错误，自动将流量路由到健康的节点。
- **负载均衡**：通过多种策略分配请求，避免单点过载。
- **双部署模式**：
  1. **Azure API Management (APIM)**：Azure 原生全托管解决方案。
  2. **LiteLLM on AKS**：基于开源 LiteLLM 的高性价比 Kubernetes 部署方案。

## 📂 项目结构

### 1. Azure API Management (APIM)
文件位于 [APIM/](./APIM/) 目录。

该方案利用 Azure 原生 API 管理服务创建网关，统一管理后端 Azure OpenAI 节点。

- **核心脚本**：`deploy_aoai_nlb.py` - 全自动化部署脚本。
- **创建资源**：APIM 实例、Managed Identity、自定义 Policy。
- **优势**：全托管服务，高可用，与 Azure 生态集成度高，无需维护底层设施。

### 2. LiteLLM on AKS
文件位于 [LiteLLM/](./LiteLLM/) 目录。

该方案将开源的 [LiteLLM](https://github.com/BerriAI/litellm) 代理部署在 Azure Kubernetes Service (AKS) 上。所有组件通过 Managed Identity 进行安全隔离。

- **核心脚本**：`deploy_mi_aks_litellm.py` - 全自动化部署脚本。
- **创建资源**：AKS 集群、PostgreSQL（日志/状态存储）、负载均衡器、VMSS。
- **优势**：高度可定制，社区活跃，性价比高（尤其是在小规模场景下）。

## 💰 成本分析 (估算)

以下是两种方案的月预估成本分析。价格基于 **East US 2** 区域（2026年参考价），实际费用可能随区域和使用量波动。

### 方案 1: Azure API Management (Standard v2)

部署脚本中默认使用了 APIM 的 **Standard v2** SKU，该 SKU 适用于流量较大的生产级负载（约 $700/月）。

| 资源项 | SKU | 单价 (估算) | 用量 | 月费用 |
| :--- | :--- | :--- | :--- | :--- |
| **API Management** | Standard v2 | ~$0.96 / 小时 | 730 小时 | **~$700.00** |
| **流量数据** | - | 极低 | - | (包含 50M API 请求/月) |
| **合计** | | | | **~$700.00 / 月** |

*注意：**Standard v2** 提供了 5000 万次/月的 API 请求配额。如果是小规模生产环境，您可以考虑修改部署脚本，将 SKU 改为 **Basic v2** (约 $150/月，包含 1000 万次请求/月)，以大幅降低成本。*

### 方案 2: LiteLLM on AKS

LiteLLM 方案部署了一个轻量级 AKS 集群。参数均在 `LiteLLM/deploy_mi_aks_litellm.py` 定义。

*   **VM 规格**: `Standard_B2als_v2` (ARM64, 2 vCPU, 4GB RAM)
*   **节点数**: 1

| 资源项 | 规格 / SKU | 单价 (估算) | 月费用 |
| :--- | :--- | :--- | :--- |
| **AKS 集群管理** | 标准层 (SLA) | ~$0.10 / 小时 | ~$73.00 |
| **虚拟机** | Standard_B2als_v2 | ~$0.023 / 小时 | ~$17.00 |
| **托管磁盘** | Standard SSD (128GB) | ~$0.06 / GB | ~$8.00 |
| **负载均衡器** | Standard Load Balancer | ~$0.025 / 小时 | ~$18.00 |
| **公网 IP** | Standard Public IP | ~$0.005 / 小时 | ~$3.65 |
| **合计** | | | **~$119.65 / 月** |

### 总结

- **最具性价比**：**LiteLLM on AKS** (~$120/月) 适合中小型规模或对成本敏感的场景。
- **最省心维护**：**APIM** (~$700/月) 适合企业级生产环境，尤其是需要零服务器运维的场景。

---

*免责声明：以上价格仅供参考，不作为最终计费依据。最新定价请访问 [Azure 定价计算器](https://azure.microsoft.com/zh-cn/pricing/calculator/)。*
