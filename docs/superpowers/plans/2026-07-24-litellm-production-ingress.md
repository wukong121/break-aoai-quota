# LiteLLM Production Ingress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace LiteLLM's public `LoadBalancer:4000` endpoint with an AKS Application Routing TLS Ingress.

**Architecture:** Validate production configuration, enable Application Routing with Key Vault access, create a ClusterIP LiteLLM Service and a TLS Ingress. The operator points an Alibaba Cloud DNS A record at the Ingress address.

**Tech Stack:** Python, Kubernetes Python client, Azure CLI, AKS Application Routing NGINX, Azure Key Vault, unittest.

## Global Constraints

- Require `LITELLM_HOSTNAME`, `LITELLM_KEYVAULT_NAME`, `LITELLM_KEYVAULT_CERT_NAME`, and non-default `LITELLM_MASTER_KEY` / `PG_PASSWORD`.
- LiteLLM Service is `ClusterIP`; the ingress load balancer is the sole public endpoint.
- The Ingress uses versionless Key Vault certificate URI and `webapprouting.kubernetes.azure.com`.
- Do not create Front Door, Application Gateway, WAF, Alibaba DNS records, or an Azure managed PostgreSQL instance.
- Preserve all existing unrelated workspace changes.
- State the November 2026 support horizon for Application Routing NGINX and the required Gateway API migration plan.

---

### Task 1: Add test-covered production settings and resource builders

**Files:**
- Modify: `LiteLLM/deploy_mi_aks_litellm.py:54-76, 550-624`
- Create: `tests/test_litellm_production_ingress.py`

**Interfaces:**
- `validate_production_settings(settings: dict[str, Any]) -> None`
- `normalize_keyvault_certificate_uri(uri: str) -> str`
- `build_litellm_ingress(hostname: str, certificate_uri: str, allowed_cidrs: str) -> k8s_client.V1Ingress`
- `build_litellm_service() -> k8s_client.V1Service`

- [ ] Write tests that reject missing/default secrets, assert `ClusterIP`, and assert a TLS Ingress host, Key Vault URI annotation, HTTP-to-HTTPS redirect and optional CIDR annotation.
- [ ] Run `python -m unittest tests.test_litellm_production_ingress -v`; expect failure before implementation.
- [ ] Add the environment-backed settings and builders. Use TLS secret `keyvault-litellm-ingress`; use backend `litellm-mi-proxy:4000`; add the CIDR annotation only for a nonempty value.
- [ ] Re-run the test; expect PASS.
- [ ] Commit only `LiteLLM/deploy_mi_aks_litellm.py` and `tests/test_litellm_production_ingress.py` with message `feat: add LiteLLM production ingress resources`.

### Task 2: Enable Application Routing and deploy through the hostname

**Files:**
- Modify: `LiteLLM/deploy_mi_aks_litellm.py:208-228, 391-449, 683-856`
- Modify: `tests/test_litellm_production_ingress.py`

**Interfaces:**
- `AzureResourceManager.enable_application_routing(aks_name: str, resource_group: str, keyvault_name: str) -> str`
- `KubernetesManager.apply_ingress(ingress: k8s_client.V1Ingress) -> None`
- `KubernetesManager.wait_for_ingress_address(name: str, timeout: int = 300) -> Optional[str]`

- [ ] Write a mocked Azure CLI test asserting `az keyvault show` obtains the vault resource ID and `az aks approuting enable --nginx External --enable-kv --attach-kv <id>` executes.
- [ ] Run the focused test; expect failure before implementation.
- [ ] Implement the Azure CLI call, obtain and normalize the certificate ID from `az keyvault certificate show`, apply/wait for Ingress, and set public base URL to `https://{LITELLM_HOSTNAME}`.
- [ ] Remove the proxy-service External IP lookup. Skip public smoke test only when `LITELLM_SKIP_PUBLIC_SMOKE_TEST=true`, but always print the Ingress address and the required DNS A record target.
- [ ] Set default LiteLLM replicas to 2, HTTP readiness/liveness probes, resource requests/limits, `run_as_non_root`, no privilege escalation and dropped capabilities. Add TCP probes to the one-replica PostgreSQL deployment.
- [ ] Run `python -m unittest tests.test_litellm_production_ingress tests.test_litellm_subscription -v`; expect PASS.
- [ ] Commit only changed production script and test with message `feat: deploy LiteLLM through AKS application routing`.

### Task 3: Write the production architecture document

**Files:**
- Create: `LiteLLM/PRODUCTION_ARCHITECTURE_ZH.md`

- [ ] Document an ASCII flow diagram, public/private traffic boundaries, TLS and Key Vault flow, Managed Identity roles, Master Key vs Virtual Key responsibility, CIDR behavior, monitoring and backup needs, non-HA PostgreSQL limitation, rollback and NGINX migration deadline.
- [ ] Run `rg -n "LoadBalancer:4000|http://<AKS LoadBalancer" LiteLLM/PRODUCTION_ARCHITECTURE_ZH.md`; expect no matches.
- [ ] Commit with message `docs: add LiteLLM production architecture guide`.

### Task 4: Write the Alibaba Cloud custom domain runbook and refresh READMEs

**Files:**
- Create: `LiteLLM/CUSTOM_DOMAIN_SETUP_ZH.md`
- Modify: `LiteLLM/README.md`
- Modify: `LiteLLM/README_ZH.md`

- [ ] Document domain/subdomain setup, certificate issue/import to Key Vault, environment variables, deployment, Ingress IP lookup, Alibaba Cloud A record, HTTPS + Virtual Key verification, troubleshooting and rollback. State that Alibaba registration itself does not require ICP filing for an overseas AKS endpoint.
- [ ] Replace direct `http://<AKS LoadBalancer IP>:4000` instructions with `https://<LITELLM_HOSTNAME>` and link both guides.
- [ ] Run `rg -n "http://<AKS LoadBalancer IP>" LiteLLM/README.md LiteLLM/README_ZH.md LiteLLM/CUSTOM_DOMAIN_SETUP_ZH.md`; expect no matches.
- [ ] Commit documentation with message `docs: document LiteLLM custom domain deployment`.

### Task 5: Verify the completed change

**Files:**
- Verify: `LiteLLM/deploy_mi_aks_litellm.py`
- Verify: `tests/test_litellm_production_ingress.py`
- Verify: `LiteLLM/PRODUCTION_ARCHITECTURE_ZH.md`
- Verify: `LiteLLM/CUSTOM_DOMAIN_SETUP_ZH.md`

- [ ] Run `python -m unittest discover -s tests -v`; expect PASS.
- [ ] Run `python LiteLLM/deploy_mi_aks_litellm.py --help`; expect exit code 0.
- [ ] Run `git diff --check` and `git status --short`; expect no whitespace errors and no staging of pre-existing changes.
