# LiteLLM on Azure Kubernetes Service (AKS)

在 AKS 群集上自动化部署 [LiteLLM](https://github.com/BerriAI/litellm)，以实现高效且高性价比的 OpenAI 负载均衡工作流。

## 📂 结构
* `deploy_mi_aks_litellm.py`: AKS 主部署脚本。
* `azure-openai.json`: 声明式的模型映射与配置。

*注：测试与依赖项已整合至项目根目录 (`../tests/` 与 `../requirements.txt`)。*

## ⚙️ 使用说明
```bash
# 1. 安装根目录依赖
pip install -r ../requirements.txt

# 2. 部署基础架构及 LiteLLM
python deploy_mi_aks_litellm.py

# 3. 从终端输出中获取负载均衡 IP，启动测试
python ../tests/test_all_deployments_all.py --config azure-openai.json --base-url "http://<aks-lb-ip>:4000" --api-key "<lite-llm-key>"
```
