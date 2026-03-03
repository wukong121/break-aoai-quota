# Azure OpenAI Load Balancer Solution (APIM)

This project provides an automated solution to deploy **Azure API Management (APIM)** as a smart load balancer for Azure OpenAI services. It aggregates multiple Azure OpenAI instances across different regions into a single endpoint, providing high availability, automatic failover, and support for both Azure Native and OpenAI Compatible SDKs.

## 🚀 Key Advantages

*   **Smart Load Balancing**: Automatically distributes traffic across multiple Azure OpenAI backends (Round-Robin).
*   **High Availability & Resilience**: Built-in retry logic for `429 Too Many Requests` and `5xx Server Errors`. If one region is down or throttled, traffic seamlessly shifts to another.
*   **Dual Protocol Support**:
    *   **Native Azure Mode**: Works with standard Azure OpenAI SDKs (`/openai/deployments/...`).
    *   **OpenAI Compatible Mode**: Works with standard OpenAI libraries (`/v1/chat/completions`), simplifying migration from non-Azure OpenAI.
    *   **Responses API Support**: Provides support for the new low-latency Responses API (`/openai/responses` or `/v1/responses`) with automatic version handling.
*   **Zero-Key Backend Security**: Uses **Managed Identity** between APIM and Azure OpenAI endpoints. You do not need to manage backend API keys in your applications.
*   **Multi-Model Support**: Validated support for GPT-4, GPT-4o, Reasoning models (o1/preview), and DALL-E 3 (Image Generation).
*   **Cross-Subscription Capability**: Can aggregate backend resources from multiple Azure Subscriptions seamlessly.

---

## 🛠️ Prerequisites

*   **Python 3.10+**
*   **Azure CLI** (logged in via `az login`)
*   **Access to AWS OpenAI Resources**: You should have created Azure OpenAI resources in different regions (e.g., East US, Sweden Central).

## 📂 Project Structure

*   `deploy_aoai_nlb.py`: Main deployment script using Azure SDK for Python.
*   `azure-openai.json`: Configuration file for backends and models.
*   `test_apim.py`: Unified testing tool associated with the deployment.
*   `requirements.txt`: Python dependencies.

---

## ⚙️ Configuration

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Edit `azure-openai.json`**:
    Configure your APIM name, target resource group, and list your backend OpenAI resources. Parameters `reverse_mode` is no longer needed as both modes are deployed.

    ```json
    {
        "apim_name": "my-unique-apim-name",
        "apim_resource_group": "my-resource-group",
        "region": "eastus2",
        "azure-openai-list": [
            {
                "name": "instance-eastus",
                "endpoint": "https://instance-eastus.openai.azure.com/",
                "resource_group": "rg-eastus"
            },
            {
                "name": "instance-europe-sub2",
                "endpoint": "https://instance-europe.openai.azure.com/",
                "resource_group": "rg-europe",
                "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  // Optional: For cross-subscription resources
            }
        ],
        "deployment_list": [
            { "model": "gpt-4o", "deployment_name": "gpt-4o" },
            { "model": "gpt-image-1", "deployment_name": "dall-e-3" }
        ]
    }
    ```

---

## 📦 Deployment

Run the Python deployment script. It will create the APIM service (if not exists), configure backends, create the routing policies, and enable/assign Managed Identity.

```bash
python deploy_aoai_nlb.py
```

*Note: The initial creation of an APIM service can take 30-45 minutes. Updates to APIs/Policies are fast.*

**What happens during deployment:**
1.  Creates/Updates APIM Service.
2.  Enables User-Assigned Managed Identity on APIM and assigns the `Cognitive Services OpenAI User` role to the identity on your backend OpenAI resources (cross-subscription supported).
3.  Creates two APIs:
    *   `/openai` (Native + Responses API support)
    *   `/v1` (OpenAI Compatible + Responses API rewrite)

---

## 🧪 Testing & Usage

Once deployed, the script outputs the **Gateway URL**. You need to obtain a **Subscription Key** from the Azure Portal (API Management -> Subscriptions).

Use the included `test_apim.py` script to verify the deployment.

### 1. Test Azure Native Mode
Simulates the Azure OpenAI SDK behavior.

```bash
python test_apim.py \
  --mode azure \
  --url "https://<your-apim-name>.azure-api.net/" \
  --key "<your-subscription-key>"
```

### 2. Test OpenAI Compatible Mode
Simulates the standard OpenAI SDK behavior (replacing `https://api.openai.com` with your APIM endpoint).

```bash
python test_apim.py \
  --mode openai \
  --url "https://<your-apim-name>.azure-api.net/" \
  --key "<your-subscription-key>"
```

---

## 📝 Policy Details

The solution deploys a smart XML policy performing the following:

1.  **Backend Rotation**: Uses a round-robin algorithm to pick a healthy backend.
2.  **Retry Strategy**: 
    *   Retries on `429` (Throttling) and `5xx` errors.
    *   Uses exponential backoff logic.
3.  **Authentication**: Strips the incoming API Key and injects the Managed Identity token (`Authorization: Bearer <token>`).
4.  **Rewrite (OpenAI Mode)**: Dynamically maps `/v1/chat/completions` requests to the Azure specific format `/openai/deployments/{model}/chat/completions?api-version=...`.
