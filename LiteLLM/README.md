# LiteLLM on Azure Kubernetes Service (AKS)

Deploy [LiteLLM](https://github.com/BerriAI/litellm) on an AKS cluster to balance OpenAI workloads efficiently and cost-effectively.

## 📂 Structure
* `deploy_mi_aks_litellm.py`: Main AKS deployment script.
* `azure-openai.json`: Declarative model configuration.

*Note: Tests and dependencies are located at the project root (`../tests/` and `../requirements.txt`).*

## ⚙️ Usage
```bash
# 1. Install dependencies from root
pip install -r ../requirements.txt

# 2. Deploy infrastructure and LiteLLM
python deploy_mi_aks_litellm.py

# 3. Retrieve deployment load-balancer IP from the output, and test
python ../tests/test_all_deployments_all.py --config azure-openai.json --base-url "http://<aks-lb-ip>:4000" --api-key "<lite-llm-key>"
```
