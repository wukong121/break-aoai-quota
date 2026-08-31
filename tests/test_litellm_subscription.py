import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

import yaml

from LiteLLM.deploy_mi_aks_litellm import (
    AzureResourceManager,
    KubernetesManager,
    build_litellm_deployment,
    generate_litellm_config,
    get_subscription_id,
    parse_affinity_checks,
)


class SubscriptionSelectionTests(unittest.TestCase):
    def test_environment_variable_wins(self):
        with patch.dict(os.environ, {"AZURE_SUBSCRIPTION_ID": "env-sub"}, clear=False), \
             patch("LiteLLM.deploy_mi_aks_litellm.subprocess.check_output") as check_output:
            self.assertEqual(get_subscription_id(), "env-sub")
            check_output.assert_not_called()

    def test_falls_back_to_active_azure_cli_subscription(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch(
                 "LiteLLM.deploy_mi_aks_litellm.subprocess.check_output",
                 return_value="cli-sub\n",
             ) as check_output:
            self.assertEqual(get_subscription_id(), "cli-sub")
            check_output.assert_called_once_with(
                ["az", "account", "show", "--query", "id", "-o", "tsv"],
                text=True,
            )

    def test_generated_config_enables_responses_affinity(self):
        config = {
            "azure-openai-list": [
                {
                    "name": "aoai-a",
                    "endpoint": "https://aoai-a.openai.azure.com/",
                }
            ],
            "deployment_list": [
                {"model": "gpt-test", "deployment_name": "gpt-test"}
            ],
        }
        settings = {
            "affinity_checks": [
                "responses_api_deployment_check",
                "deployment_affinity",
                "session_affinity",
            ],
            "deployment_affinity_ttl_seconds": 1800,
        }

        generated = yaml.safe_load(generate_litellm_config(config, settings))

        self.assertEqual(
            generated["router_settings"]["optional_pre_call_checks"],
            settings["affinity_checks"],
        )
        self.assertEqual(
            generated["router_settings"]["deployment_affinity_ttl_seconds"],
            1800,
        )
        self.assertEqual(
            generated["general_settings"]["maximum_spend_logs_retention_period"],
            "7d",
        )
        self.assertEqual(
            generated["general_settings"]["maximum_spend_logs_retention_interval"],
            "1d",
        )
        self.assertFalse(
            generated["general_settings"]["store_prompts_in_spend_logs"]
        )

    def test_existing_pvc_expands_only_when_enabled(self):
        manager = KubernetesManager.__new__(KubernetesManager)
        manager.namespace = "litellm"
        manager.core_v1 = Mock()
        manager.core_v1.read_namespaced_persistent_volume_claim.return_value = (
            SimpleNamespace(
                spec=SimpleNamespace(
                    resources=SimpleNamespace(requests={"storage": "1Gi"})
                )
            )
        )

        manager.apply_pvc("pg-data", "20Gi")
        manager.core_v1.patch_namespaced_persistent_volume_claim.assert_not_called()

        manager.apply_pvc("pg-data", "20Gi", expand_existing=True)
        manager.core_v1.patch_namespaced_persistent_volume_claim.assert_called_once_with(
            "pg-data",
            "litellm",
            {"spec": {"resources": {"requests": {"storage": "20Gi"}}}},
        )

    def test_litellm_deployment_rolls_when_config_hash_changes(self):
        first = build_litellm_deployment("litellm:test", "hash-a", "secret-hash")
        second = build_litellm_deployment("litellm:test", "hash-b", "secret-hash")

        self.assertEqual(first.spec.template.metadata.annotations["litellm.config-hash"], "hash-a")
        self.assertEqual(second.spec.template.metadata.annotations["litellm.config-hash"], "hash-b")
        self.assertNotEqual(
            first.spec.template.metadata.annotations["litellm.config-hash"],
            second.spec.template.metadata.annotations["litellm.config-hash"],
        )

    def test_affinity_checks_reject_unknown_values(self):
        with self.assertRaisesRegex(ValueError, "Unsupported LITELLM_AFFINITY_CHECKS"):
            parse_affinity_checks("responses_api_deployment_check,unknown-check")

    def test_existing_vmss_identity_skips_update(self):
        identity_id = (
            "/subscriptions/sub/resourceGroups/rg/providers/"
            "Microsoft.ManagedIdentity/userAssignedIdentities/litellm"
        )
        manager = AzureResourceManager.__new__(AzureResourceManager)
        manager.compute_client = SimpleNamespace(
            virtual_machine_scale_sets=SimpleNamespace(
                get=Mock(
                    return_value=SimpleNamespace(
                        identity=SimpleNamespace(
                            type="UserAssigned",
                            user_assigned_identities={identity_id.lower(): {}},
                        )
                    )
                ),
                begin_update=Mock(),
            )
        )

        manager.assign_identity_to_vmss("vmss", "node-rg", identity_id)

        manager.compute_client.virtual_machine_scale_sets.begin_update.assert_not_called()

    def test_raises_when_no_subscription_can_be_resolved(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch(
                 "LiteLLM.deploy_mi_aks_litellm.subprocess.check_output",
                 side_effect=OSError("az unavailable"),
             ):
            with self.assertRaisesRegex(RuntimeError, "AZURE_SUBSCRIPTION_ID"):
                get_subscription_id()


if __name__ == "__main__":
    unittest.main()