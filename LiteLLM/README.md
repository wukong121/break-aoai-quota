# LiteLLM Managed Identity AKS Deployment

This directory contains a Python automation script for deploying LiteLLM Proxy to Azure Kubernetes Service (AKS) and configuring Managed Identity (MI) for keyless access to Azure OpenAI resources.

## ✨ Features

- **One-Click Automation**: Automatically creates/updates Azure resources (Resource Group, Managed Identity, AKS).
- **Secure Integration**:
  - Automatically attaches the User-Assigned Managed Identity to the AKS node VMSS.
  - Automatically assigns the `Cognitive Services OpenAI User` role to the Managed Identity on target Azure OpenAI resources (supports cross-subscription).
- **Built-in Database**: Automatically deploys PostgreSQL in AKS for LiteLLM data storage.
- **Auto Configuration**: Automatically generates LiteLLM configuration files and K8s resources (Secret, ConfigMap) based on the configuration file (e.g., `azure-openai.json`).
- **Dual Mode Support**: Supports both native OpenAI format (`/v1/chat/completions`) and Azure OpenAI format (`/openai/deployments/...`) simultaneously.
- **Self-Check**: Runs smoke tests automatically after deployment to verify API connectivity.

## 📋 Prerequisites

1. **Environmental Dependencies**:
   - Python 3.8+
   - Azure CLI (`az`)
   - `kubectl` (Recommended to pre-install; the script primarily uses the `kubernetes` Python library)

2. **Azure Login**:
   Ensure you have logged in via Azure CLI and selected the correct subscription:
   ```bash
   az login
   az account set --subscription <YOUR_SUBSCRIPTION_ID>
   ```

3. **Install Dependencies**:
   ```bash
   pip3 install -r requirements.txt
   ```

## ⚙️ Configuration File (azure-openai.json)

Before running the script, please prepare the `azure-openai.json` file. This file defines the target Azure OpenAI resources and the deployment model list.

**Example Structure:**

```json
{
  "region": "swedencentral",
  "apim_resource_group": "litellm-resources-rg",
  "managed_identity": "litellm-identity-prod",
  "azure-openai-list": [
    {
      "name": "my-aoai-resource-1",
      "resource_group": "ai-resources-rg",
      "subscription_id": "00000000-0000-0000-0000-000000000000" // Optional, must specify if cross-subscription
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

## 🚀 Usage

Run the Python script to start the deployment. By default, it reads `azure-openai.json` in the current directory.

```bash
python3 deploy_mi_aks_litellm.py
```

Or specify the configuration file path:

```bash
python3 deploy_mi_aks_litellm.py /path/to/my-config.json
```

### Environment Variables Configuration

You can override default configurations using environment variables:

| Variable Name | Default Value | Description |
|--------|--------|------|
| `MI_NAME` | `litellm-managed-identity` | Managed Identity Name (if not specified in json) |
| `AKS_NAME` | `litellm-mi-aks` | AKS Cluster Name |
| `AKS_NODE_COUNT` | `1` | AKS Node Count |
| `AKS_VM_SIZE` | `Standard_B2als_v2` | AKS Node VM Size |
| `AKS_NAMESPACE` | `litellm` | Kubernetes Namespace |
| `LITELLM_IMAGE` | `micl/litellm:mi-fix-image-gen` | LiteLLM Image Address |
| `LITELLM_MASTER_KEY` | `sk-local-mi-test-key` | LiteLLM Master Key (used for management and API calls) |
| `PG_STORAGE` | `1Gi` | PostgreSQL PVC Size |
| `RUN_SMOKE_TEST` | `true` | Whether to run smoke tests after deployment |

## 🔍 Verify Deployment

After the script executes successfully, it will output the service access information. You can manually execute the following commands to check the cluster status:

```bash
# Get Pods and Services
kubectl -n litellm get pods,svc

# View LiteLLM Logs
kubectl -n litellm logs deploy/litellm-mi-proxy --tail=100 -f
```

### Smoke Test

The script automatically runs smoke tests on the deployed service, verifying the availability of `/v1/responses` (OpenAI style) and `/openai/v1/responses` (Azure style) endpoints.

If you want to manually run the full test suit, you can use the provided test script:

```bash
python3 test_all_deployments_responses.py
```

## 🏗️ Architecture Description

The script creates the following resources in AKS:

1. **Namespace**: `litellm`
2. **ConfigMap**: `litellm-config` (Contains generated `litellm.config.yaml`)
3. **Secret**: `litellm-env` (Contains sensitive info like `AZURE_CLIENT_ID`, `DATABASE_URL`, `LITELLM_MASTER_KEY` )
4. **PostgreSQL**:
   - Deployment: `postgres`
   - Service: `postgres`
   - PVC: `pg-data`
5. **LiteLLM Proxy**:
   - Deployment: `litellm-mi-proxy`
   - Service: `litellm-mi-proxy` (Type: LoadBalancer)
