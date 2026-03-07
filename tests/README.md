# End-to-End Tests

This directory contains the testing scripts used to validate the deployment and functionality of different proxy gateways (LiteLLM and APIM) ensuring they correctly route the models.

## Files

- `test_all_deployments_all.py`: A unified end-to-end testing script that evaluates the routing and functionality for both LiteLLM and APIM deployments. It tests standard OpenAI Format and Azure OpenAI Format across text, image, and video models.

## Requirements

Ensure your environment is set up and your proxy is running. The scripts only use standard Python standard libraries (`urllib`, `json`, `argparse`, etc.) so you do not need to install any external dependencies (like `requests` or `openai`).

## Usage

### Test Gateway Proxy (LiteLLM / APIM)

You will need the `azure-openai.json` configuration file, the Base URL of the proxy, and the configured API Key.

```bash
python test_all_deployments_all.py \
  --config ../LiteLLM/azure-openai.json \
  --base-url http://<GATEWAY_EXTERNAL_IP_OR_DOMAIN>:<PORT> \
  --api-key <YOUR_GATEWAY_API_KEY>
```

**Parameters:**
- `--config`: Path to the `azure-openai.json` which contains the model deployment mapping.
- `--base-url`: The endpoint where the gateway (LiteLLM / APIM) is listening.
- `--api-key`: The API/Master key for the gateway authentication. Note: it automatically sends both `Authorization: Bearer` and `api-key:` headers to support different gateway types natively.
- `--prompt`: (Optional) Custom test prompt for text tests. Default: "请只回复: ok".
- `--image-prompt`: (Optional) Custom test prompt for image generation tests.

## Features Verified

1. **Text Models (Chat API)**: Tested against both standard OpenAI path (`/v1/chat/completions`) and Azure OpenAI path (`/openai/deployments/...`).
2. **Image Models**: Tested against both standard OpenAI path (`/v1/images/generations`) and Azure OpenAI path (`/openai/deployments/...`).
3. **Video Models (Sora)**: Tested by verifying its presence in the proxy's model registry list (`/v1/models`).
