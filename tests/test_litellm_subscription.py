import os
import unittest
from unittest.mock import patch

from LiteLLM.deploy_mi_aks_litellm import build_litellm_deployment, get_subscription_id


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

    def test_litellm_deployment_rolls_when_config_hash_changes(self):
        first = build_litellm_deployment("litellm:test", "hash-a")
        second = build_litellm_deployment("litellm:test", "hash-b")

        self.assertEqual(first.spec.template.metadata.annotations["litellm.config-hash"], "hash-a")
        self.assertEqual(second.spec.template.metadata.annotations["litellm.config-hash"], "hash-b")
        self.assertNotEqual(
            first.spec.template.metadata.annotations["litellm.config-hash"],
            second.spec.template.metadata.annotations["litellm.config-hash"],
        )
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