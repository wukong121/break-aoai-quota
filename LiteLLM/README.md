# LiteLLM on Azure Kubernetes Service (AKS)

Deploy [LiteLLM](https://github.com/BerriAI/litellm) on AKS to load-balance Azure OpenAI deployments, retry transient failures, track spend, and manage per-user or per-team budgets.

## Structure

- `deploy_mi_aks_litellm.py`: Deploys AKS, Managed Identity, PostgreSQL, and the LiteLLM Proxy.
- `azure-openai.json`: Azure OpenAI resources and deployment mappings.
- `USER_BUDGET_AND_MODEL_ACCESS_ZH.md`: Chinese guide for users, teams, virtual keys, budgets, and model access.
- FOUNDRY_MODEL_SYNC_ZH.md: Guide for syncing new Foundry/Azure OpenAI deployments into LiteLLM.
- `litellm.config.yaml`: Generated LiteLLM configuration; do not edit it manually because the deployment script regenerates it.

*Tests and dependencies are located at the project root (`../tests/` and `../requirements.txt`).*

## Usage

```powershell
# 1. Install dependencies from the project root
cd ..
python -m pip install -r .\requirements.txt

# 2. Sign in and explicitly select the subscription used for AKS
az login
$env:AZURE_SUBSCRIPTION_ID = "<AKS-subscription-id>"

# 3. Deploy from the LiteLLM directory
cd .\LiteLLM
python .\deploy_mi_aks_litellm.py
```

The script creates or reuses the Resource Group, Managed Identity, and AKS cluster named by the configuration. In the LiteLLM configuration, `apim_resource_group` is a legacy field name: it means the AKS/Managed Identity Resource Group, not an APIM Resource Group.

The default AKS settings are:

```text
Region: eastus2
Nodes: 1
VM size: Standard_B2s
```

Override the VM size when needed:

```powershell
$env:AKS_VM_SIZE = "Standard_B2ms"
```

Each `azure-openai-list[].subscription_id` is used to grant the Managed Identity access to the corresponding Azure OpenAI Resource, including cross-subscription resources.

## User budgets and model access

Open:

```text
http://<AKS LoadBalancer IP>:4000/ui
```

Use `Internal Users`, `Teams`, and `Virtual Keys` to configure individual budgets, shared team budgets, model allowlists, and spend tracking. See [`USER_BUDGET_AND_MODEL_ACCESS_ZH.md`](./USER_BUDGET_AND_MODEL_ACCESS_ZH.md) for the detailed guide.

## Testing

The actual unified test file is `../tests/test_all_deployments.py`:

```powershell
python ..\tests\test_all_deployments.py `
  --config .\azure-openai.json `
  --base-url "http://<AKS LoadBalancer IP>:4000" `
  --api-key "<LiteLLM Virtual Key>" `
  --prompt ok
```

The test validates both OpenAI-style and Azure OpenAI-style Chat routes. On Windows consoles using `cp1252`, pass an ASCII prompt to avoid an encoding error before the first request.

## Notes

- Do not distribute the Admin Master Key; create a Virtual Key for each user.
- The default service uses a public LoadBalancer. Production deployments should add TLS, network restrictions, and a strong random key.
- PostgreSQL is currently a single in-cluster replica for lightweight deployments; production environments should use a highly available database with backups.

## Custom domain and HTTPS

To serve LiteLLM at `https://litellm.your-domain.com` instead of `http://<IP>:4000`, set `LITELLM_HOSTNAME` and the script will automatically configure ingress-nginx + cert-manager (Let's Encrypt):

```powershell
$env:LITELLM_HOSTNAME = "litellm.example.com"   # enables ingress mode
$env:LETSENCRYPT_EMAIL = "you@example.com"       # required for Let's Encrypt
python .\deploy_mi_aks_litellm.py
```

- Prerequisites: Helm and kubectl installed locally.
- The script prints the ingress public IP; create an A record for it in your DNS provider. The certificate is issued automatically after DNS propagates.
- Without `LITELLM_HOSTNAME`, the script keeps the existing `LoadBalancer:4000` behavior.

See [`CUSTOM_DOMAIN_SETUP_ZH.md`](./CUSTOM_DOMAIN_SETUP_ZH.md) for the full runbook (buy domain, DNS, certificate, verification, troubleshooting, rollback).