# Azure OpenAI Load Balancer Solution (APIM)

Deploy **Azure API Management (APIM)** as a smart load balancer for Azure OpenAI. It aggregates multiple regional instances into a single endpoint, providing high availability, automatic failover, and support for native and OpenAI-compatible SDKs.

## 🚀 Key Advantages
* **Smart Load Balancing & Resilience**: Retries precisely on `429`, `500`, and `503` errors.
* **Dual Protocol Support**: Native Azure Mode and OpenAI Compatible Mode.
* **Model Separation Support**: Explicit fallback for specific reasoning and rendering models (like Sora wildcard mapping).
* **Responses API Support**: Ultra-low latency endpoint optimizations.
* **Zero-Key Security**: Managed Identity backend communication.

## 📂 Structure
* `deploy_mi_apim.py`: Main deployment script using Azure SDK.
* `azure-openai.json`: Config file for backends and models.

*Note: Tests and dependencies are located at the project root (`../tests/` and `../requirements.txt`).*

## ⚙️ Usage
```bash
# 1. Install dependencies from root
pip install -r ../requirements.txt

# 2. Configure azure-openai.json
# 3. Deploy
python deploy_mi_apim.py

# 4. Test Deployment
python ../tests/test_all_deployments_all.py --config azure-openai.json --base-url "https://<your-apim>.azure-api.net" --api-key "<subscription-key>"
```
