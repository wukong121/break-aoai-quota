import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from APIM.deploy_mi_apim import (  # noqa: E402
    AZURE_CHAT_API_VERSION,
    AZURE_EMBEDDINGS_API_VERSION,
    AZURE_IMAGES_API_VERSION,
    AZURE_RESPONSES_PREVIEW_API_VERSION,
    AzureDeploymentManager,
)


class DeployMiApimTests(unittest.TestCase):
    def setUp(self):
        self.valid_config = {
            "apim_name": "aoai-apim-prod",
            "apim_resource_group": "rg-aoai-prod",
            "region": "eastus2",
            "azure-openai-list": [
                {
                    "name": "aoai-eastus2-1",
                    "endpoint": "https://aoai-eastus2-1.openai.azure.com/",
                    "resource_group": "rg-aoai-eastus2",
                },
                {
                    "name": "aoai-eastus2-2",
                    "endpoint": "https://aoai-eastus2-2.openai.azure.com/",
                    "resource_group": "rg-aoai-eastus2",
                    "subscription_id": "11111111-1111-1111-1111-111111111111",
                },
            ],
            "deployment_list": [
                {
                    "model": "gpt-4o",
                    "deployment_name": "gpt-4o-prod",
                },
                {
                    "model": "text-embedding-3-small",
                    "deployment_name": "embed-prod",
                },
                {
                    "model": "gpt-image-1",
                    "deployment_name": "image-prod",
                },
            ],
        }

    def _new_manager(self, config=None):
        manager = AzureDeploymentManager.__new__(AzureDeploymentManager)
        manager.config = copy.deepcopy(config or self.valid_config)
        manager.model_alias_map = AzureDeploymentManager.build_model_alias_map(manager.config["deployment_list"])
        manager.identity_client_id = "test-client-id"
        return manager

    def test_validate_config_accepts_valid_config(self):
        AzureDeploymentManager.validate_config(copy.deepcopy(self.valid_config))

    def test_validate_config_rejects_invalid_endpoint(self):
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["azure-openai-list"][0]["endpoint"] = "http://example.com"

        with self.assertRaisesRegex(ValueError, "invalid endpoint"):
            AzureDeploymentManager.validate_config(invalid_config)

    def test_validate_config_rejects_ambiguous_openai_model_mapping(self):
        invalid_config = copy.deepcopy(self.valid_config)
        invalid_config["deployment_list"].append(
            {
                "model": "gpt-4o",
                "deployment_name": "gpt-4o-canary",
            }
        )

        with self.assertRaisesRegex(ValueError, "Ambiguous OpenAI-compatible model mapping"):
            AzureDeploymentManager.validate_config(invalid_config)

    def test_build_api_operations_covers_openai_and_azure_responses(self):
        manager = self._new_manager()
        openai_operations = manager._build_api_operations(is_openai_mode=True)
        azure_operations = manager._build_api_operations(is_openai_mode=False)

        openai_routes = {(item["method"], item["url_template"]) for item in openai_operations}
        azure_routes = {(item["method"], item["url_template"]) for item in azure_operations}

        self.assertIn(("POST", "/embeddings"), openai_routes)
        self.assertIn(("POST", "/responses"), openai_routes)
        self.assertIn(("GET", "/responses/{responseId}"), openai_routes)
        self.assertIn(("DELETE", "/responses/{responseId}"), openai_routes)
        self.assertIn(("GET", "/responses/{responseId}/input_items"), openai_routes)

        self.assertIn(("POST", "/responses"), azure_routes)
        self.assertIn(("GET", "/responses/{responseId}"), azure_routes)
        self.assertIn(("DELETE", "/responses/{responseId}"), azure_routes)
        self.assertIn(("GET", "/responses/{responseId}/input_items"), azure_routes)
        self.assertIn(("POST", "/v1/responses"), azure_routes)
        self.assertIn(("GET", "/v1/responses/{responseId}"), azure_routes)
        self.assertIn(("DELETE", "/v1/responses/{responseId}"), azure_routes)
        self.assertIn(("GET", "/v1/responses/{responseId}/input_items"), azure_routes)
        self.assertIn(("GET", "/v1/models"), azure_routes)

    def test_policy_uses_round_robin_and_model_alias_rewrite(self):
        manager = self._new_manager()
        openai_policy = manager.create_load_balancing_policy_xml(
            ["aoai-backend-0", "aoai-backend-1"],
            is_openai_mode=True,
        )
        azure_policy = manager.create_load_balancing_policy_xml(
            ["aoai-backend-0", "aoai-backend-1"],
            is_openai_mode=False,
        )

        self.assertIn("cache-lookup-value", openai_policy)
        self.assertIn("cache-store-value", openai_policy)
        self.assertNotIn("new Random", openai_policy)
        self.assertIn(AZURE_CHAT_API_VERSION, openai_policy)
        self.assertIn(AZURE_EMBEDDINGS_API_VERSION, openai_policy)
        self.assertIn(AZURE_IMAGES_API_VERSION, openai_policy)
        self.assertIn("gpt-4o-prod", openai_policy)
        self.assertIn('/v1/responses', openai_policy)

        self.assertIn(AZURE_RESPONSES_PREVIEW_API_VERSION, azure_policy)
        self.assertIn('/responses/{responseId}', azure_policy)
        self.assertIn('/v1/responses', azure_policy)


if __name__ == "__main__":
    unittest.main()