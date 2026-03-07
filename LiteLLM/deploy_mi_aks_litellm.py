#!/usr/bin/env python3
"""
LiteLLM Managed Identity AKS Deployment Script

This script deploys LiteLLM Proxy to Azure Kubernetes Service (AKS) with
Managed Identity authentication for Azure OpenAI resources.

Usage:
    python deploy_mi_aks_litellm.py [config.json]

Requirements:
    pip install -r requirements.txt
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
import yaml
from azure.identity import DefaultAzureCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.containerservice import ContainerServiceClient
from azure.mgmt.msi import ManagedServiceIdentityClient
from azure.mgmt.resource import ResourceManagementClient
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException


# ═══════════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "mi_name": os.environ.get("MI_NAME", "litellm-managed-identity"),
    "aks_name": os.environ.get("AKS_NAME", "litellm-mi-aks"),
    "aks_node_count": int(os.environ.get("AKS_NODE_COUNT", "1")),
    "aks_vm_size": os.environ.get("AKS_VM_SIZE", "Standard_B2als_v2"),
    "aks_namespace": os.environ.get("AKS_NAMESPACE", "litellm"),
    "litellm_image": os.environ.get("LITELLM_IMAGE", "micl/litellm:mi-fix-image-gen"),
    "litellm_master_key": os.environ.get("LITELLM_MASTER_KEY", "sk-local-mi-test-key"),
    "azure_scope": os.environ.get("AZURE_SCOPE", "https://cognitiveservices.azure.com/.default"),
    "azure_api_version": os.environ.get("AZURE_API_VERSION", ""),
    "openai_role_name": os.environ.get("OPENAI_ROLE_NAME", "Cognitive Services OpenAI User"),
    "run_smoke_test": os.environ.get("RUN_SMOKE_TEST", "true").lower() == "true",
    "pg_user": os.environ.get("PG_USER", "litellm"),
    "pg_password": os.environ.get("PG_PASSWORD", "litellm-local-dev"),
    "pg_db": os.environ.get("PG_DB", "litellm"),
    "pg_storage": os.environ.get("PG_STORAGE", "1Gi"),
}

OPENAI_USER_ROLE_ID = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"  # Cognitive Services OpenAI User


# ═══════════════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════════════

def log(message: str, level: str = "INFO") -> None:
    """Print a log message with timestamp."""
    print(f"[{level}] {message}")


def load_config(config_path: str) -> dict[str, Any]:
    """Load and validate the configuration JSON file."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    required_fields = ["region", "apim_resource_group", "azure-openai-list", "deployment_list"]
    for field in required_fields:
        if field not in cfg or not cfg[field]:
            raise ValueError(f"Config must include non-empty '{field}'")

    return cfg


def extract_resource_name_from_endpoint(endpoint: str) -> Optional[str]:
    """Extract Azure resource name from endpoint URL."""
    match = re.match(r"^https://([^.]+)\.openai\.azure\.com", endpoint)
    return match.group(1) if match else None


def get_subscription_id() -> str:
    """Get the current Azure subscription ID."""
    credential = DefaultAzureCredential()
    from azure.mgmt.subscription import SubscriptionClient
    sub_client = SubscriptionClient(credential)
    subs = list(sub_client.subscriptions.list())
    if not subs:
        raise RuntimeError("No Azure subscriptions found")
    return subs[0].subscription_id


# ═══════════════════════════════════════════════════════════════════════════════
# Azure Resource Management
# ═══════════════════════════════════════════════════════════════════════════════

class AzureResourceManager:
    """Manages Azure resources using Azure SDK."""

    def __init__(self, subscription_id: str):
        self.subscription_id = subscription_id
        self.credential = DefaultAzureCredential()
        self.resource_client = ResourceManagementClient(self.credential, subscription_id)
        self.msi_client = ManagedServiceIdentityClient(self.credential, subscription_id)
        self.aks_client = ContainerServiceClient(self.credential, subscription_id)
        self.compute_client = ComputeManagementClient(self.credential, subscription_id)
        self.auth_client = AuthorizationManagementClient(self.credential, subscription_id)

    def ensure_resource_group(self, name: str, location: str) -> None:
        """Create resource group if it doesn't exist."""
        log(f"Ensuring resource group exists: {name} ({location})")
        self.resource_client.resource_groups.create_or_update(
            name,
            {"location": location}
        )

    def ensure_managed_identity(self, name: str, resource_group: str, location: str) -> dict:
        """Create or get managed identity."""
        log(f"Ensuring managed identity exists: {name}")
        try:
            identity = self.msi_client.user_assigned_identities.get(resource_group, name)
            log(f"Managed identity already exists: {name}")
        except Exception:
            identity = self.msi_client.user_assigned_identities.create_or_update(
                resource_group,
                name,
                {"location": location}
            )
            log(f"Created managed identity: {name}")

        return {
            "client_id": identity.client_id,
            "principal_id": identity.principal_id,
            "resource_id": identity.id,
        }

    def ensure_aks_cluster(
        self,
        name: str,
        resource_group: str,
        location: str,
        node_count: int,
        vm_size: str,
    ) -> dict:
        """Create or get AKS cluster."""
        log(f"Ensuring AKS exists: {name}")
        try:
            cluster = self.aks_client.managed_clusters.get(resource_group, name)
            log(f"AKS already exists: {name}")
        except Exception:
            log(f"Creating AKS cluster: {name} (this may take 5-10 minutes)")
            poller = self.aks_client.managed_clusters.begin_create_or_update(
                resource_group,
                name,
                {
                    "location": location,
                    "dns_prefix": f"{name}-dns",
                    "agent_pool_profiles": [
                        {
                            "name": "nodepool1",
                            "count": node_count,
                            "vm_size": vm_size,
                            "mode": "System",
                        }
                    ],
                    "identity": {"type": "SystemAssigned"},
                    "sku": {"name": "Base", "tier": "Standard"},
                }
            )
            cluster = poller.result()
            log(f"Created AKS cluster: {name}")

        return {
            "node_resource_group": cluster.node_resource_group,
            "fqdn": cluster.fqdn,
        }

    def get_aks_credentials(self, name: str, resource_group: str) -> None:
        """Fetch AKS credentials and configure kubectl."""
        log("Fetching AKS credentials")
        # Use az CLI for kubeconfig merge (SDK doesn't directly support this)
        subprocess.run(
            ["az", "aks", "get-credentials", "--name", name, "--resource-group", resource_group, "--overwrite-existing"],
            check=True,
            capture_output=True,
        )

    def get_vmss_in_resource_group(self, resource_group: str) -> str:
        """Get the first VMSS name in a resource group."""
        vmss_list = list(self.compute_client.virtual_machine_scale_sets.list(resource_group))
        if not vmss_list:
            raise RuntimeError(f"No VMSS found in resource group: {resource_group}")
        return vmss_list[0].name

    def assign_identity_to_vmss(self, vmss_name: str, resource_group: str, identity_resource_id: str) -> None:
        """Assign user-assigned managed identity to VMSS."""
        log(f"Assigning user-assigned MI to AKS node VMSS: {vmss_name}")
        vmss = self.compute_client.virtual_machine_scale_sets.get(resource_group, vmss_name)

        # Prepare identity update
        user_identities = vmss.identity.user_assigned_identities or {} if vmss.identity else {}
        if identity_resource_id not in user_identities:
            user_identities[identity_resource_id] = {}

        identity_type = "SystemAssigned, UserAssigned" if vmss.identity and vmss.identity.type == "SystemAssigned" else "UserAssigned"

        poller = self.compute_client.virtual_machine_scale_sets.begin_update(
            resource_group,
            vmss_name,
            {
                "identity": {
                    "type": identity_type,
                    "user_assigned_identities": user_identities,
                }
            }
        )
        poller.result()
        log(f"Verified: MI is attached to VMSS.")

    def assign_role_on_scope(
        self,
        principal_id: str,
        scope: str,
        role_name: str,
        subscription_id: str,
    ) -> bool:
        """Assign a role to a principal on a specific scope."""
        auth_client = AuthorizationManagementClient(self.credential, subscription_id)

        # Check if role already assigned
        existing = list(auth_client.role_assignments.list_for_scope(
            scope,
            filter=f"principalId eq '{principal_id}'"
        ))

        for assignment in existing:
            if role_name.lower() in assignment.role_definition_id.lower() or OPENAI_USER_ROLE_ID in assignment.role_definition_id:
                log(f"Role already assigned on scope")
                return False

        # Get role definition ID
        role_defs = list(auth_client.role_definitions.list(
            scope,
            filter=f"roleName eq '{role_name}'"
        ))
        if not role_defs:
            log(f"WARNING: Role '{role_name}' not found", "WARN")
            return False

        role_def_id = role_defs[0].id

        # Create role assignment
        import uuid
        assignment_name = str(uuid.uuid4())
        auth_client.role_assignments.create(
            scope,
            assignment_name,
            RoleAssignmentCreateParameters(
                role_definition_id=role_def_id,
                principal_id=principal_id,
                principal_type="ServicePrincipal",
            )
        )
        return True


# ═══════════════════════════════════════════════════════════════════════════════
# Kubernetes Operations
# ═══════════════════════════════════════════════════════════════════════════════

class KubernetesManager:
    """Manages Kubernetes resources."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        k8s_config.load_kube_config()
        self.core_v1 = k8s_client.CoreV1Api()
        self.apps_v1 = k8s_client.AppsV1Api()

    def ensure_namespace(self) -> None:
        """Create namespace if it doesn't exist."""
        try:
            self.core_v1.read_namespace(self.namespace)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespace(
                    k8s_client.V1Namespace(metadata=k8s_client.V1ObjectMeta(name=self.namespace))
                )

    def apply_configmap(self, name: str, data: dict[str, str]) -> None:
        """Create or update a ConfigMap."""
        configmap = k8s_client.V1ConfigMap(
            metadata=k8s_client.V1ObjectMeta(name=name, namespace=self.namespace),
            data=data,
        )
        try:
            self.core_v1.read_namespaced_config_map(name, self.namespace)
            self.core_v1.replace_namespaced_config_map(name, self.namespace, configmap)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_config_map(self.namespace, configmap)
            else:
                raise

    def apply_secret(self, name: str, data: dict[str, str]) -> None:
        """Create or update a Secret."""
        secret = k8s_client.V1Secret(
            metadata=k8s_client.V1ObjectMeta(name=name, namespace=self.namespace),
            string_data=data,
        )
        try:
            self.core_v1.read_namespaced_secret(name, self.namespace)
            self.core_v1.replace_namespaced_secret(name, self.namespace, secret)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_secret(self.namespace, secret)
            else:
                raise

    def apply_pvc(self, name: str, storage: str) -> None:
        """Create PersistentVolumeClaim if not exists."""
        pvc = k8s_client.V1PersistentVolumeClaim(
            metadata=k8s_client.V1ObjectMeta(name=name, namespace=self.namespace),
            spec=k8s_client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=k8s_client.V1ResourceRequirements(requests={"storage": storage}),
            ),
        )
        try:
            self.core_v1.read_namespaced_persistent_volume_claim(name, self.namespace)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_persistent_volume_claim(self.namespace, pvc)
            else:
                raise

    def apply_deployment(self, deployment: k8s_client.V1Deployment) -> None:
        """Create or update a Deployment."""
        name = deployment.metadata.name
        try:
            self.apps_v1.read_namespaced_deployment(name, self.namespace)
            self.apps_v1.replace_namespaced_deployment(name, self.namespace, deployment)
        except ApiException as e:
            if e.status == 404:
                self.apps_v1.create_namespaced_deployment(self.namespace, deployment)
            else:
                raise

    def apply_service(self, service: k8s_client.V1Service) -> None:
        """Create or update a Service."""
        name = service.metadata.name
        try:
            existing = self.core_v1.read_namespaced_service(name, self.namespace)
            # Preserve clusterIP for update
            service.spec.cluster_ip = existing.spec.cluster_ip
            self.core_v1.replace_namespaced_service(name, self.namespace, service)
        except ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_service(self.namespace, service)
            else:
                raise

    def wait_for_deployment(self, name: str, timeout: int = 600) -> bool:
        """Wait for a deployment to be ready."""
        log(f"Waiting for deployment rollout: {name}")
        start = time.time()
        while time.time() - start < timeout:
            try:
                deployment = self.apps_v1.read_namespaced_deployment(name, self.namespace)
                if deployment.status.ready_replicas == deployment.spec.replicas:
                    return True
            except ApiException:
                pass
            time.sleep(5)
        return False

    def get_pod_logs(self, deployment_name: str, tail_lines: int = 10) -> str:
        """Get logs from a deployment's pod."""
        try:
            pods = self.core_v1.list_namespaced_pod(
                self.namespace,
                label_selector=f"app={deployment_name}"
            )
            if pods.items:
                return self.core_v1.read_namespaced_pod_log(
                    pods.items[0].metadata.name,
                    self.namespace,
                    tail_lines=tail_lines,
                )
        except ApiException:
            pass
        return ""

    def get_service_external_ip(self, name: str, timeout: int = 150) -> Optional[str]:
        """Get the external IP of a LoadBalancer service."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                svc = self.core_v1.read_namespaced_service(name, self.namespace)
                if svc.status.load_balancer.ingress:
                    return svc.status.load_balancer.ingress[0].ip
            except ApiException:
                pass
            time.sleep(5)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# LiteLLM Config Generation
# ═══════════════════════════════════════════════════════════════════════════════

def generate_litellm_config(cfg: dict[str, Any]) -> str:
    """Generate LiteLLM configuration YAML."""
    resources = cfg["azure-openai-list"]
    deployments = cfg["deployment_list"]

    model_list = []
    for deployment in deployments:
        model = deployment["model"]
        deployment_name = deployment["deployment_name"]
        alias = deployment_name

        # Decide API version based on model name
        model_low = model.lower()
        if model_low.startswith(("gpt-image-", "dall-e", "sora")):
            resolved_api_version = "2025-04-01-preview"
        else:
            # Let's try matching the test script's expectation of standard endpoints vs responses api
            resolved_api_version = "2025-04-01-preview"

        for resource in resources:
            endpoint = resource.get("endpoint", "")
            if not endpoint:
                name = resource["name"]
                endpoint = f"https://{name}.openai.azure.com/"

            model_list.append({
                "model_name": alias,
                "litellm_params": {
                    "model": f"azure/{model}",
                    "base_model": model,
                    "deployment_id": deployment_name,
                    "api_base": endpoint,
                    "api_version": resolved_api_version,
                },
            })

    config = {
        "model_list": model_list,
        "litellm_settings": {
            "enable_azure_ad_token_refresh": True,
        },
        "router_settings": {
            "routing_strategy": "simple-shuffle",
            "num_retries": 2,
        },
    }

    return yaml.dump(config, default_flow_style=False, sort_keys=False)


# ═══════════════════════════════════════════════════════════════════════════════
# Kubernetes Resource Builders
# ═══════════════════════════════════════════════════════════════════════════════

def build_postgres_deployment(pg_user: str, pg_password: str, pg_db: str) -> k8s_client.V1Deployment:
    """Build PostgreSQL deployment."""
    return k8s_client.V1Deployment(
        metadata=k8s_client.V1ObjectMeta(name="postgres"),
        spec=k8s_client.V1DeploymentSpec(
            replicas=1,
            selector=k8s_client.V1LabelSelector(match_labels={"app": "postgres"}),
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(labels={"app": "postgres"}),
                spec=k8s_client.V1PodSpec(
                    containers=[
                        k8s_client.V1Container(
                            name="postgres",
                            image="postgres:16-alpine",
                            ports=[k8s_client.V1ContainerPort(container_port=5432)],
                            env=[
                                k8s_client.V1EnvVar(name="POSTGRES_DB", value=pg_db),
                                k8s_client.V1EnvVar(name="POSTGRES_USER", value=pg_user),
                                k8s_client.V1EnvVar(name="POSTGRES_PASSWORD", value=pg_password),
                                k8s_client.V1EnvVar(name="PGDATA", value="/var/lib/postgresql/data/pgdata"),
                            ],
                            volume_mounts=[
                                k8s_client.V1VolumeMount(name="pg-data", mount_path="/var/lib/postgresql/data")
                            ],
                            resources=k8s_client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "500m", "memory": "256Mi"},
                            ),
                        )
                    ],
                    volumes=[
                        k8s_client.V1Volume(
                            name="pg-data",
                            persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(claim_name="pg-data"),
                        )
                    ],
                ),
            ),
        ),
    )


def build_postgres_service() -> k8s_client.V1Service:
    """Build PostgreSQL service."""
    return k8s_client.V1Service(
        metadata=k8s_client.V1ObjectMeta(name="postgres"),
        spec=k8s_client.V1ServiceSpec(
            selector={"app": "postgres"},
            ports=[k8s_client.V1ServicePort(port=5432, target_port=5432)],
        ),
    )


def build_litellm_deployment(image: str) -> k8s_client.V1Deployment:
    """Build LiteLLM proxy deployment."""
    return k8s_client.V1Deployment(
        metadata=k8s_client.V1ObjectMeta(name="litellm-mi-proxy"),
        spec=k8s_client.V1DeploymentSpec(
            replicas=1,
            selector=k8s_client.V1LabelSelector(match_labels={"app": "litellm-mi-proxy"}),
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(labels={"app": "litellm-mi-proxy"}),
                spec=k8s_client.V1PodSpec(
                    containers=[
                        k8s_client.V1Container(
                            name="litellm",
                            image=image,
                            image_pull_policy="Always",
                            command=["litellm"],
                            args=["--config", "/app/config/config.yaml", "--port", "4000"],
                            ports=[k8s_client.V1ContainerPort(container_port=4000)],
                            env_from=[
                                k8s_client.V1EnvFromSource(secret_ref=k8s_client.V1SecretEnvSource(name="litellm-env"))
                            ],
                            volume_mounts=[
                                k8s_client.V1VolumeMount(
                                    name="litellm-config",
                                    mount_path="/app/config/config.yaml",
                                    sub_path="config.yaml",
                                )
                            ],
                        )
                    ],
                    volumes=[
                        k8s_client.V1Volume(
                            name="litellm-config",
                            config_map=k8s_client.V1ConfigMapVolumeSource(name="litellm-config"),
                        )
                    ],
                ),
            ),
        ),
    )


def build_litellm_service() -> k8s_client.V1Service:
    """Build LiteLLM proxy service."""
    return k8s_client.V1Service(
        metadata=k8s_client.V1ObjectMeta(name="litellm-mi-proxy"),
        spec=k8s_client.V1ServiceSpec(
            selector={"app": "litellm-mi-proxy"},
            ports=[k8s_client.V1ServicePort(port=4000, target_port=4000, protocol="TCP")],
            type="LoadBalancer",
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Smoke Test
# ═══════════════════════════════════════════════════════════════════════════════

def run_smoke_test(
    base_url: str,
    master_key: str,
    model_alias: str,
    azure_api_version: str,
) -> bool:
    """Run smoke tests against LiteLLM proxy."""
    log("Running smoke tests for Chat API (OpenAI + Azure OpenAI style)")

    headers = {
        "Authorization": f"Bearer {master_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_alias,
        "messages": [{"role": "user", "content": "reply only: ok"}],
        "max_tokens": 32,
    }

    # Test OpenAI-style endpoint
    try:
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if not resp.ok:
            log(f"OpenAI-style /v1/chat/completions failed. status={resp.status_code}", "ERROR")
            return False
    except Exception as e:
        log(f"OpenAI-style /v1/chat/completions failed: {e}", "ERROR")
        return False

    # Test Azure-style endpoint
    azure_url = f"{base_url}/openai/deployments/{model_alias}/chat/completions"
    if azure_api_version:
        azure_url += f"?api-version={azure_api_version}"

    try:
        resp = requests.post(
            azure_url,
            headers=headers,
            json=payload,
            timeout=60,
        )
        if not resp.ok:
            log(f"Azure-style chat format failed. status={resp.status_code}", "ERROR")
            return False
    except Exception as e:
        log(f"Azure-style chat format failed: {e}", "ERROR")
        return False

    log("Smoke test passed: /v1/chat/completions and Azure-style chat are both available")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Main Deployment Flow
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Deploy LiteLLM with Managed Identity to AKS")
    parser.add_argument(
        "config",
        nargs="?",
        default=str(Path(__file__).parent / "azure-openai.json"),
        help="Path to azure-openai.json config file",
    )
    args = parser.parse_args()

    # Load configuration
    cfg = load_config(args.config)
    region = cfg["region"]
    rg_name = cfg["apim_resource_group"]
    mi_name = cfg.get("managed_identity") or DEFAULT_CONFIG["mi_name"]

    # Merge with defaults
    settings = {**DEFAULT_CONFIG}
    if cfg.get("managed_identity"):
        settings["mi_name"] = cfg["managed_identity"]

    log(f"Configuration loaded from: {args.config}")
    log(f"Region: {region}, Resource Group: {rg_name}, MI: {mi_name}")

    # Get current subscription
    subscription_id = get_subscription_id()
    log(f"Using subscription: {subscription_id}")

    # Initialize Azure manager
    azure_mgr = AzureResourceManager(subscription_id)

    # Step 1: Ensure resource group
    azure_mgr.ensure_resource_group(rg_name, region)

    # Step 2: Ensure managed identity
    mi_info = azure_mgr.ensure_managed_identity(mi_name, rg_name, region)
    log(f"MI Client ID: {mi_info['client_id']}")

    # Step 3: Ensure AKS cluster
    aks_info = azure_mgr.ensure_aks_cluster(
        settings["aks_name"],
        rg_name,
        region,
        settings["aks_node_count"],
        settings["aks_vm_size"],
    )

    # Step 4: Get AKS credentials
    azure_mgr.get_aks_credentials(settings["aks_name"], rg_name)

    # Step 5: Assign MI to VMSS
    vmss_name = azure_mgr.get_vmss_in_resource_group(aks_info["node_resource_group"])
    azure_mgr.assign_identity_to_vmss(vmss_name, aks_info["node_resource_group"], mi_info["resource_id"])

    # Step 6: Assign RBAC roles on AOAI resources
    log(f"Granting '{settings['openai_role_name']}' on each Azure OpenAI resource")
    for aoai in cfg["azure-openai-list"]:
        endpoint = aoai.get("endpoint", "")
        aoai_name = extract_resource_name_from_endpoint(endpoint)
        if not aoai_name:
            log(f"WARN: Cannot extract resource name from endpoint for {aoai['name']}, skipping.", "WARN")
            continue

        aoai_rg = aoai["resource_group"]
        aoai_sub = aoai.get("subscription_id") or subscription_id

        log(f"Processing AOAI resource: {aoai_name} (rg={aoai_rg}, sub={aoai_sub})")

        # Verify resource exists
        try:
            cog_client = CognitiveServicesManagementClient(azure_mgr.credential, aoai_sub)
            cog_client.accounts.get(aoai_rg, aoai_name)
        except Exception:
            log(f"WARN: AOAI resource not found, skipping: {aoai_name}", "WARN")
            continue

        # Assign role
        scope = f"/subscriptions/{aoai_sub}/resourceGroups/{aoai_rg}/providers/Microsoft.CognitiveServices/accounts/{aoai_name}"
        if azure_mgr.assign_role_on_scope(
            mi_info["principal_id"],
            scope,
            settings["openai_role_name"],
            aoai_sub,
        ):
            log(f"Assigned role on: {aoai_name}")
        else:
            log(f"Role already assigned on: {aoai_name}")

    # Step 7: Generate LiteLLM config
    litellm_config_yaml = generate_litellm_config(cfg)
    config_path = Path(args.config).parent / "litellm.config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(litellm_config_yaml)
    log(f"Generated LiteLLM config: {config_path}")

    # Step 8: Deploy to Kubernetes
    k8s_mgr = KubernetesManager(settings["aks_namespace"])
    k8s_mgr.ensure_namespace()

    # ConfigMap
    k8s_mgr.apply_configmap("litellm-config", {"config.yaml": litellm_config_yaml})

    # PostgreSQL
    log(f"Deploying PostgreSQL in namespace {settings['aks_namespace']}...")
    database_url = f"postgresql://{settings['pg_user']}:{settings['pg_password']}@postgres.{settings['aks_namespace']}.svc.cluster.local:5432/{settings['pg_db']}"
    k8s_mgr.apply_pvc("pg-data", settings["pg_storage"])
    k8s_mgr.apply_deployment(build_postgres_deployment(settings["pg_user"], settings["pg_password"], settings["pg_db"]))
    k8s_mgr.apply_service(build_postgres_service())

    if not k8s_mgr.wait_for_deployment("postgres", timeout=120):
        log("PostgreSQL deployment failed to become ready", "ERROR")
        sys.exit(1)
    log("PostgreSQL is ready.")

    # Secret
    k8s_mgr.apply_secret("litellm-env", {
        "AZURE_CREDENTIAL": "ManagedIdentityCredential",
        "AZURE_CLIENT_ID": mi_info["client_id"],
        "AZURE_SCOPE": settings["azure_scope"],
        "AZURE_API_VERSION": settings["azure_api_version"],
        "LITELLM_MASTER_KEY": settings["litellm_master_key"],
        "DATABASE_URL": database_url,
    })

    # LiteLLM
    k8s_mgr.apply_deployment(build_litellm_deployment(settings["litellm_image"]))
    k8s_mgr.apply_service(build_litellm_service())

    if not k8s_mgr.wait_for_deployment("litellm-mi-proxy", timeout=600):
        log("LiteLLM deployment failed to become ready", "ERROR")
        sys.exit(1)

    # Wait for app to fully start
    log("Waiting for LiteLLM application to start (Prisma migrations + Uvicorn)...")
    for _ in range(60):
        logs = k8s_mgr.get_pod_logs("litellm-mi-proxy", tail_lines=10)
        if "Uvicorn running" in logs:
            break
        time.sleep(5)

    # Step 9: Get external IP
    external_ip = k8s_mgr.get_service_external_ip("litellm-mi-proxy")
    base_url = f"http://{external_ip}:4000" if external_ip else "(pending)"

    # Step 10: Smoke test
    if settings["run_smoke_test"] and external_ip:
        first_deployment = cfg["deployment_list"][0]["deployment_name"]
        model_alias = first_deployment

        # Wait a bit for service to be reachable
        time.sleep(10)

        if not run_smoke_test(base_url, settings["litellm_master_key"], model_alias, settings["azure_api_version"]):
            log("Smoke test failed", "ERROR")
            sys.exit(1)

    # Step 11: Print summary
    print()
    print("═" * 63)
    print("  LiteLLM Proxy — Deployment Complete")
    print("═" * 63)
    print()
    print(f"  Web UI URL        : {base_url}/ui")
    print(f"  Web UI Username   : admin")
    print(f"  Web UI Password   : {settings['litellm_master_key']}")
    print()
    print(f"  API Base URL      : {base_url}")
    print(f"  API Key           : {settings['litellm_master_key']}")
    print()
    print(f"  Managed Identity  : {mi_name} (client_id: {mi_info['client_id']})")
    print(f"  AKS Cluster       : {settings['aks_name']} ({settings['aks_vm_size']} x{settings['aks_node_count']})")
    print(f"  Namespace         : {settings['aks_namespace']}")
    print(f"  Database          : PostgreSQL (in-cluster)")
    print()
    print("═" * 63)


if __name__ == "__main__":
    main()
