# Azure OpenAI Load Balancer & Quota Breaker

This project provides a comprehensive solution to bypass the quota limits of single Azure subscriptions for Azure OpenAI Service. By implementing a reverse proxy with load balancing capabilities, it distributes requests across multiple Azure OpenAI resources. It also robustly handles common errors like **429 (Too Many Requests)**, **500**, and **503**.

## 🌟 Key Features

- **Quota Bypass**: Aggregates throughput from multiple Azure OpenAI resources/subscriptions.
- **Error Handling**: Automatic retry logic for 429, 500, and 503 errors.
- **Model Compatibility**: Support for routing standard interactions, Image endpoints (DALL-E), and specially handling structure endpoints for models like Sora.
- **Protocol Flexibility**: Supports Azure OpenAI native format APIs, OpenAI compatible format APIs, and the ultra-low latency Response API mode.
- **Multiple Deployment Options**:
  1. **Azure API Management (APIM)**: A fully managed, cloud-native Azure solution.
  2. **LiteLLM on AKS**: A cost-effective, open-source solution running on Azure Kubernetes Service.

## 📂 Project Structure

- **`requirements.txt`**: Consolidated dependencies for deploying both APIM and LiteLLM projects.
- **`tests/`**: Unified E2E testing framework to validate models and APIs against any deployed solution.

### 1. Azure API Management (APIM)
Located in the [APIM/](./APIM/) directory.

This solution uses Azure's native API Management service to create a gateway that manages traffic to your backend Azure OpenAI endpoints.
- **Key File**: `deploy_mi_apim.py` - Automated deployment script.
- **Resources Created**: Azure API Management Service, Managed Identities, Backend policies.

### 2. LiteLLM on AKS
Located in the [LiteLLM/](./LiteLLM/) directory.

This solution deploys [LiteLLM](https://github.com/BerriAI/litellm), an open-source OpenAI proxy, onto an Azure Kubernetes Service (AKS) cluster.
- **Key File**: `deploy_mi_aks_litellm.py` - Automated deployment script.
- **Resources Created**: AKS Cluster, PostgreSQL, Load Balancer.

## ⚙️ Quick Start

**1. Install Global Dependencies:**
```bash
pip install -r requirements.txt
```

**2. Follow specific deployments:**
- Navigate to `APIM/` or `LiteLLM/` and follow their respective `README.md` to run the deployment scripts.

**3. Run Unified Tests:**
- Once endpoints are provisioned, use `tests/test_all_deployments_all.py` to validate API configurations (Dual format logic).
