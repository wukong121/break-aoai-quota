# Explicit Azure Subscription Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the LiteLLM deployment script use an explicitly selected Azure subscription instead of arbitrarily choosing the first visible subscription.

**Architecture:** `get_subscription_id()` will resolve the deployment subscription in this order: `AZURE_SUBSCRIPTION_ID`, Azure CLI's active subscription, then a clear error. Per-resource `subscription_id` values in `azure-openai-list` remain unchanged for cross-subscription Azure OpenAI RBAC.

**Tech Stack:** Python standard library, Azure Identity SDK, Azure Management SDK, `unittest`.

## Global Constraints

- Do not hard-code a tenant or subscription ID in source or configuration.
- Preserve the existing `azure-openai-list[].subscription_id` behavior.
- Do not change the user's existing configuration values.

---

### Task 1: Add subscription-resolution regression tests

**Files:**
- Create: `tests/test_litellm_subscription.py`

**Interfaces:**
- Consumes: `LiteLLM.deploy_mi_aks_litellm.get_subscription_id`.
- Produces: Tests covering environment-variable precedence, Azure CLI fallback, and missing-subscription failure.

- [ ] **Step 1: Write tests for the required resolution behavior**

```python
import os
import unittest
from unittest.mock import patch

from LiteLLM.deploy_mi_aks_litellm import get_subscription_id


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
```

- [ ] **Step 2: Run the new tests and confirm they fail before implementation**

Run: `python -m unittest tests.test_litellm_subscription -v`

Expected: FAIL because the current implementation queries `SubscriptionClient` rather than `AZURE_SUBSCRIPTION_ID` or `az account show`.

### Task 2: Implement explicit subscription resolution

**Files:**
- Modify: `LiteLLM/deploy_mi_aks_litellm.py:93-102`

**Interfaces:**
- Consumes: `AZURE_SUBSCRIPTION_ID` and `az account show --query id -o tsv`.
- Produces: `get_subscription_id() -> str` returning the deployment subscription ID or raising `RuntimeError`.

- [ ] **Step 1: Prefer the environment variable**

```python
subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
if subscription_id:
    return subscription_id
```

- [ ] **Step 2: Fall back to Azure CLI's active subscription**

```python
try:
    result = subprocess.check_output(
        ["az", "account", "show", "--query", "id", "-o", "tsv"],
        text=True,
    ).strip()
except (OSError, subprocess.CalledProcessError) as exc:
    raise RuntimeError(
        "Cannot determine Azure subscription. Set AZURE_SUBSCRIPTION_ID or run az login."
    ) from exc

if result:
    return result
```

- [ ] **Step 3: Raise a clear error for empty CLI output**

```python
raise RuntimeError(
    "Cannot determine Azure subscription. Set AZURE_SUBSCRIPTION_ID or run az login."
)
```

- [ ] **Step 4: Run the new tests and confirm they pass**

Run: `python -m unittest tests.test_litellm_subscription -v`

Expected: PASS for all three tests.

### Task 3: Verify the complete test suite and configuration preservation

**Files:**
- Verify: `LiteLLM/azure-openai.json`
- Verify: `tests/test_litellm_subscription.py`
- Verify: `LiteLLM/deploy_mi_aks_litellm.py`

- [ ] **Step 1: Run all repository unit tests**

Run: `python -m unittest discover -s tests -v`

Expected: Existing tests and subscription-selection tests pass.

- [ ] **Step 2: Confirm the configured subscription is supplied at runtime**

Run: `$env:AZURE_SUBSCRIPTION_ID = "a6f5bde8-aff7-46a2-a902-78014c84f875"; python LiteLLM/deploy_mi_aks_litellm.py --help`

Expected: The script starts argument parsing without embedding or modifying the subscription ID in repository files.
