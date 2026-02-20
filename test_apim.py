import os
import json
import logging
import time
import argparse
from openai import AzureOpenAI, OpenAI

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_config(config_file="azure-openai.json"):
    """Load deployment configuration from JSON file."""
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return json.load(f)
    return {"deployment_list": []}

def run_tests(mode, url, key):
    """
    Run tests against the specified APIM endpoint.
    
    Args:
        mode (str): 'azure' or 'openai'
        url (str): The APIM endpoint URL (e.g., https://my-apim.azure-api.net/)
        key (str): The APIM Subscription Key
    """
    
    # 1. Load Configurations (to know which models to test)
    config = load_config()
    deployments = config.get('deployment_list', [])
    
    if not deployments:
        logger.warning("No deployment list found in azure-openai.json. Defaulting to ['gpt-4o'].")
        deployments = [{"model": "gpt-4o", "deployment_name": "gpt-4o"}]

    logger.info(f"Starting APIM Test in [{mode.upper()}] mode against {url}")

    # 2. Initialize Client
    client = None
    if mode.lower() == 'azure':
        # for Azure mode, the URL usually needs /openai or is the root.
        # Ensure URL doesn't end with /openai if the client adds it, but usually AzureOpenAI client
        # takes the base endpoint and adds /openai/deployments/...
        # If the user passes https://micl.azure-api.net/openai, we should handle it.
        
        # Standardize URL: AzureOpenAI expects the resource endpoint
        # If user provides ".../openai", strip it for the client base (or keep it if APIM requires)
        # However, APIM native API is usually at https://{service}.azure-api.net/openai
        # The python SDK appends /openai/deployments...
        # So if we pass https://{service}.azure-api.net/, the SDK makes https://{service}.azure-api.net/openai/deployments...
        # If we pass https://{service}.azure-api.net/openai, the SDK makes https://{service}.azure-api.net/openai/openai/deployments... (BAD)
        
        base_endpoint = url.rstrip('/')
        if base_endpoint.endswith("/openai"):
            base_endpoint = base_endpoint[:-7]
            
        client = AzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=key,
            api_version="2024-02-15-preview"
        )
    else:
        # OpenAI Mode
        base_url = url.rstrip('/')
        if not base_url.endswith("/v1"):
            base_url += "/v1"
            
        client = OpenAI(
            base_url=base_url,
            api_key=key,
            default_headers={"api-key": key} # Inject APIM Subscription Key
        )

    # 3. Iterate and Test
    for dep in deployments:
        model_name = dep['model']
        # In Azure mode, we use deployment_name for the URL.
        # In OpenAI mode, we use the model name in the body (and APIM maps it).
        target_model = dep['deployment_name'] if mode == 'azure' else dep['model']
        
        logger.info(f"--- Testing Model: {model_name} (Target: {target_model}) ---")
        
        try:
            start_time = time.time()
            
            # Handle Image Generation Models
            if "image" in model_name.lower() or "dall-e" in model_name.lower():
                logger.info(f"Testing Image Generation for model: {model_name}")
                response = client.images.generate(
                    model=target_model,
                    prompt="A futuristic city with flying cars",
                    n=1,
                    size="1024x1024"
                )
                duration = time.time() - start_time
                logger.info(f"Success! Duration: {duration:.2f}s")
                if hasattr(response, 'data') and len(response.data) > 0:
                    logger.info(f"Image URL: {response.data[0].url}")
                continue

            # Handle Chat Models
            # We use a simple prompt
            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello! reply in 5 words."}
            ]
            
            # Special handling for reasoning models (o1, gpt-5.2-preview often need max_completion_tokens)
            # or legacy codex models.
            
            if "gpt-5.1-codex" in model_name:
                # Test Responses API
                logger.info(f"Executing Responses API Test for {model_name}...")
                
                # Responses API payload (removed 'n' parameter which caused 400 error)
                payload = {
                    "model": target_model, 
                    "input": "Write a Python function to calculate Fibonacci numbers."
                }
                
                if mode.lower() == 'azure':
                     # AzureOpenAI client appends /openai to base_url automatically.
                     # We want POST {host}/openai/responses?api-version=2025-04-01-preview
                     # But the client base_url might be {host}/openai/deployments/...
                     
                     # 1. Get the base URL without /openai or deployments
                     base_host = str(client.base_url).split("/openai")[0]
                     
                     # 2. Construct the exact URL for Responses API
                     target_url = f"{base_host}/openai/responses"
                     
                     logger.info(f"Targeting Azure Responses URL: {target_url}")
                     
                     try:
                        # Use the internal client to avoid path manipulation by the SDK wrapper
                        resp = client._client.post(
                             target_url, 
                             json=payload, 
                             headers={"api-key": os.getenv("AZURE_OPENAI_API_KEY") or args.key},
                             params={"api-version": "2025-04-01-preview"}
                        )
                        
                        if resp.status_code == 200 or resp.status_code == 201:
                            logger.info("Azure Mode Responses Success!")
                        else:
                            logger.error(f"Azure Mode Responses failed: {resp.status_code} - {resp.text}")

                     except Exception as e:
                        logger.error(f"Azure Mode Responses failed: {e}")
                        continue

                else:
                     # OpenAI Mode
                     # client.post(path="responses") -> {base}/v1/responses
                     try:
                        resp = client.post(
                             path="/responses", 
                             body=payload, 
                             cast_to=object
                        )
                        logger.info("OpenAI Mode Responses Success!")
                     except Exception as e:
                        logger.error(f"OpenAI Mode Responses failed: {e}")
                        continue
                
                logger.info(f"Success! Duration: {time.time() - start_time:.2f}s")
                # Since we cast to object (dict), print verify basic structure
                if isinstance(resp, dict):
                    resp_id = resp.get('id', 'N/A')
                    logger.info(f"Response ID: {resp_id}")
                else:
                     # If using pydantic model or similar
                     logger.info(f"Response Object: {resp}")
                continue

            if "gpt-5.2" in model_name or "o1" in model_name:
                 response = client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    max_completion_tokens=50
                )
            else:
                response = client.chat.completions.create(
                    model=target_model, 
                    messages=messages,
                    max_tokens=50
                )

            duration = time.time() - start_time
            
            content = ""
            if hasattr(response, 'choices') and len(response.choices) > 0:
                if hasattr(response.choices[0], 'message'):
                    content = response.choices[0].message.content
                else:
                    content = response.choices[0].text
                
                logger.info(f"Success! Duration: {duration:.2f}s")
                clean_content = content.replace('\n', ' ')[:100] if content else ""
                logger.info(f"Response: {clean_content}...")
            else:
                logger.warning("Response received but no choices found.")
            
        except Exception as e:
            logger.error(f"Failed to query {model_name}: {str(e)}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test APIM Load Balance with Azure OpenAI or OpenAI Client")
    parser.add_argument("--mode", required=True, choices=['azure', 'openai'], help="Test mode: 'azure' (Native) or 'openai' (Compatible)")
    parser.add_argument("--url", required=True, help="APIM Base URL (e.g., https://my-apim.azure-api.net/)")
    parser.add_argument("--key", required=True, help="APIM Subscription Key")
    
    args = parser.parse_args()
    
    run_tests(args.mode, args.url, args.key)
