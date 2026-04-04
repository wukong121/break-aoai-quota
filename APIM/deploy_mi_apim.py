import os
import sys
import json
import logging
import re
import uuid
from urllib.parse import urlparse
from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.msi import ManagedServiceIdentityClient
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.apimanagement import ApiManagementClient
from azure.mgmt.apimanagement.models import (
    ApiManagementServiceResource,
    ApiCreateOrUpdateParameter,
    BackendContract,
    PolicyContract,
    ApiManagementServiceIdentity,
    UserIdentityProperties,
    ResourceSku,
    OperationContract,
    ParameterContract,
    SubscriptionKeyParameterNamesContract
)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 常量定义
# "Cognitive Services OpenAI User" Role ID
OPENAI_USER_ROLE_ID = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
IDENTITY_NAME = "aoai-nlb-identity"
AZURE_CHAT_API_VERSION = "2024-10-21"
AZURE_EMBEDDINGS_API_VERSION = "2024-10-21"
AZURE_IMAGES_API_VERSION = "2025-04-01-preview"
AZURE_RESPONSES_PREVIEW_API_VERSION = "2025-04-01-preview"
ROUND_ROBIN_CACHE_KEY = "aoai-apim-round-robin-index"

class AzureDeploymentManager:
    def __init__(self, config_file="azure-openai.json"):
        self.config = self._load_config(config_file)
        self.model_alias_map = self.build_model_alias_map(self.config['deployment_list'])
        self.identity_client_id = None
        
        # 尝试使用 DefaultAzureCredential (支持环境变量, Managed Identity, Azure CLI 等)
        # 并显式添加 InteractiveBrowserCredential 作为后备选项，以便在本地未登录 CLI 时弹出浏览器登录
        try:
            logger.info("Auth: Attempting to acquire credentials (DefaultAzureCredential)...")
            self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        except Exception:
             logger.info("Default credentials not found. Launching interactive browser login...")
             self.credential = InteractiveBrowserCredential()
        
        # 获取 Subscription ID
        self.subscription_id = os.environ.get("AZURE_SUBSCRIPTION_ID")
        
        # 尝试通过 Azure CLI 获取默认 Subscription ID (如果环境变量未设置)
        if not self.subscription_id:
            try:
                import subprocess
                logger.info("AZURE_SUBSCRIPTION_ID not set. Trying to get from Azure CLI...")
                sub_id = subprocess.check_output("az account show --query id -o tsv", shell=True).decode().strip()
                if sub_id:
                    self.subscription_id = sub_id
                    logger.info(f"Found Subscription ID from CLI: {self.subscription_id}")
            except Exception:
                pass

        if not self.subscription_id:
             logger.error("Error: AZURE_SUBSCRIPTION_ID is missing. Please set the environment variable or log in via 'az login'.")
             # 这里不应该继续初始化 resource_client，因为它会抛错
             # 为了让后续逻辑能走下去 (可能只是验证 credential)，我们暂时赋值一个占位符，但在实际调用时会失败
             # 但 ResourceManagementClient __init__ 会检查它不为 None。
             raise ValueError("Please set AZURE_SUBSCRIPTION_ID environment variable.")

        self.resource_client = ResourceManagementClient(self.credential, self.subscription_id)
        self.msi_client = ManagedServiceIdentityClient(self.credential, self.subscription_id)
        self.auth_client = AuthorizationManagementClient(self.credential, self.subscription_id)
        self.apim_client = ApiManagementClient(self.credential, self.subscription_id)

    def _load_config(self, config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                self.validate_config(config)
                return config
        except Exception as e:
            logger.error(f"Failed to load config file: {e}")
            raise

    @staticmethod
    def validate_config(config):
        if not isinstance(config, dict):
            raise ValueError("Config file must contain a JSON object.")

        for key in ("apim_name", "apim_resource_group", "region"):
            value = config.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Config field '{key}' must be a non-empty string.")

        aoai_resources = config.get('azure-openai-list')
        if not isinstance(aoai_resources, list) or not aoai_resources:
            raise ValueError("Config field 'azure-openai-list' must be a non-empty list.")

        for index, aoai in enumerate(aoai_resources):
            if not isinstance(aoai, dict):
                raise ValueError(f"Azure OpenAI resource entry #{index + 1} must be an object.")

            for key in ("name", "endpoint", "resource_group"):
                value = aoai.get(key)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Azure OpenAI resource entry #{index + 1} is missing '{key}'.")

            endpoint = aoai['endpoint'].strip()
            parsed = urlparse(endpoint)
            if parsed.scheme != "https" or not parsed.netloc.endswith(".openai.azure.com"):
                raise ValueError(
                    f"Azure OpenAI resource entry #{index + 1} has an invalid endpoint: {endpoint}"
                )

            subscription_id = aoai.get('subscription_id')
            if subscription_id is not None and (not isinstance(subscription_id, str) or not subscription_id.strip()):
                raise ValueError(
                    f"Azure OpenAI resource entry #{index + 1} has an invalid 'subscription_id'."
                )

        deployments = config.get('deployment_list')
        if not isinstance(deployments, list) or not deployments:
            raise ValueError("Config field 'deployment_list' must be a non-empty list.")

        seen_deployments = set()
        model_to_deployment = {}

        for index, deployment in enumerate(deployments):
            if not isinstance(deployment, dict):
                raise ValueError(f"Deployment entry #{index + 1} must be an object.")

            model = deployment.get('model')
            deployment_name = deployment.get('deployment_name')

            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"Deployment entry #{index + 1} is missing 'model'.")

            if not isinstance(deployment_name, str) or not deployment_name.strip():
                raise ValueError(f"Deployment entry #{index + 1} is missing 'deployment_name'.")

            model = model.strip()
            deployment_name = deployment_name.strip()

            if deployment_name in seen_deployments:
                raise ValueError(f"Duplicate deployment_name detected: {deployment_name}")
            seen_deployments.add(deployment_name)

            existing = model_to_deployment.get(model)
            if existing and existing != deployment_name:
                raise ValueError(
                    f"Ambiguous OpenAI-compatible model mapping for '{model}': "
                    f"'{existing}' and '{deployment_name}'."
                )
            model_to_deployment[model] = deployment_name

    @staticmethod
    def build_model_alias_map(deployments):
        alias_map = {}
        for deployment in deployments:
            model = deployment['model'].strip()
            deployment_name = deployment['deployment_name'].strip()
            if model != deployment_name:
                alias_map[model] = deployment_name
        return alias_map

    @staticmethod
    def _escape_csharp_string(value):
        return value.replace('\\', '\\\\').replace('"', '\\"')

    def _build_model_resolution_policy(self, request_model_var="requestModel", resolved_var="deploymentName"):
        policy_lines = [
            f"<set-variable name=\"{resolved_var}\" value='@((string)context.Variables[\"{request_model_var}\"])' />"
        ]

        if not self.model_alias_map:
            return "\n".join(policy_lines)

        policy_lines.append("<choose>")
        for model_alias, deployment_name in sorted(self.model_alias_map.items()):
            alias = self._escape_csharp_string(model_alias)
            resolved = self._escape_csharp_string(deployment_name)
            policy_lines.append(
                f"<when condition='@((string)context.Variables[\"{request_model_var}\"] == \"{alias}\")'>"
            )
            policy_lines.append(
                f"    <set-variable name=\"{resolved_var}\" value=\"{resolved}\" />"
            )
            policy_lines.append("</when>")
        policy_lines.append("</choose>")
        return "\n".join(policy_lines)

    @staticmethod
    def _build_backend_selection_policy(backend_ids, index_var="backendIndex"):
        if not backend_ids:
            raise ValueError("At least one backend is required to generate the APIM policy.")

        policy_lines = ["<choose>"]
        for index, backend_id in enumerate(backend_ids[:-1]):
            policy_lines.append(
                f"<when condition='@((int)context.Variables[\"{index_var}\"] == {index})'>"
            )
            policy_lines.append(
                f"    <set-variable name=\"selectedBackend\" value=\"{backend_id}\" />"
            )
            policy_lines.append("</when>")

        policy_lines.append("<otherwise>")
        policy_lines.append(
            f"    <set-variable name=\"selectedBackend\" value=\"{backend_ids[-1]}\" />"
        )
        policy_lines.append("</otherwise>")
        policy_lines.append("</choose>")
        return "\n".join(policy_lines)

    def _build_api_operations(self, is_openai_mode):
        operations = []

        if is_openai_mode:
            operations.extend([
                {
                    "id": "openai-chat-completions",
                    "display_name": "OpenAI Chat Completions",
                    "method": "POST",
                    "url_template": "/chat/completions",
                    "description": "Access chat models via 'model' body parameter"
                },
                {
                    "id": "openai-embeddings",
                    "display_name": "OpenAI Embeddings",
                    "method": "POST",
                    "url_template": "/embeddings",
                    "description": "Access embedding models via 'model' body parameter"
                },
                {
                    "id": "openai-images-generations",
                    "display_name": "OpenAI Image Generations",
                    "method": "POST",
                    "url_template": "/images/generations",
                    "description": "Access image models via 'model' body parameter"
                },
                {
                    "id": "openai-responses-create",
                    "display_name": "OpenAI Responses",
                    "method": "POST",
                    "url_template": "/responses",
                    "description": "Create a response via the OpenAI-compatible API"
                },
                {
                    "id": "openai-responses-get",
                    "display_name": "OpenAI Retrieve Response",
                    "method": "GET",
                    "url_template": "/responses/{responseId}",
                    "description": "Retrieve a response via the OpenAI-compatible API"
                },
                {
                    "id": "openai-responses-delete",
                    "display_name": "OpenAI Delete Response",
                    "method": "DELETE",
                    "url_template": "/responses/{responseId}",
                    "description": "Delete a stored response via the OpenAI-compatible API"
                },
                {
                    "id": "openai-responses-input-items",
                    "display_name": "OpenAI Response Input Items",
                    "method": "GET",
                    "url_template": "/responses/{responseId}/input_items",
                    "description": "List response input items via the OpenAI-compatible API"
                },
                {
                    "id": "openai-models",
                    "display_name": "OpenAI Models",
                    "method": "GET",
                    "url_template": "/models",
                    "description": "Access models registry via the OpenAI-compatible API"
                },
            ])
        else:
            for deployment in self.config['deployment_list']:
                model_name = deployment['model']
                deployment_name = deployment['deployment_name']
                model_name_lower = model_name.lower()

                if "image" in model_name_lower or "dall-e" in model_name_lower:
                    operations.append({
                        "id": f"image-{deployment_name}".replace(".", "-").replace(" ", "-"),
                        "display_name": f"Image Generation ({deployment_name})",
                        "method": "POST",
                        "url_template": f"/deployments/{deployment_name}/images/generations",
                        "description": f"Proxy for model {model_name}"
                    })
                elif "sora" in model_name_lower or "video" in model_name_lower:
                    operations.append({
                        "id": f"sora-{deployment_name}".replace(".", "-").replace(" ", "-"),
                        "display_name": f"Sora Model ({deployment_name})",
                        "method": "POST",
                        "url_template": f"/deployments/{deployment_name}/*",
                        "description": f"Proxy for model {model_name}"
                    })
                elif "embedding" in model_name_lower:
                    operations.append({
                        "id": f"embed-{deployment_name}".replace(".", "-").replace(" ", "-"),
                        "display_name": f"Embeddings ({deployment_name})",
                        "method": "POST",
                        "url_template": f"/deployments/{deployment_name}/embeddings",
                        "description": f"Proxy for model {model_name}"
                    })
                else:
                    operations.append({
                        "id": f"chat-{deployment_name}".replace(".", "-").replace(" ", "-"),
                        "display_name": f"Chat Completion ({deployment_name})",
                        "method": "POST",
                        "url_template": f"/deployments/{deployment_name}/chat/completions",
                        "description": f"Proxy for model {model_name}"
                    })

            operations.extend([
                {
                    "id": "azure-responses-preview-create",
                    "display_name": "Azure Responses API (Preview)",
                    "method": "POST",
                    "url_template": "/responses",
                    "description": "Create a response via Azure OpenAI preview responses API"
                },
                {
                    "id": "azure-responses-preview-get",
                    "display_name": "Azure Retrieve Response (Preview)",
                    "method": "GET",
                    "url_template": "/responses/{responseId}",
                    "description": "Retrieve a response via Azure OpenAI preview responses API"
                },
                {
                    "id": "azure-responses-preview-delete",
                    "display_name": "Azure Delete Response (Preview)",
                    "method": "DELETE",
                    "url_template": "/responses/{responseId}",
                    "description": "Delete a stored response via Azure OpenAI preview responses API"
                },
                {
                    "id": "azure-responses-preview-input-items",
                    "display_name": "Azure Response Input Items (Preview)",
                    "method": "GET",
                    "url_template": "/responses/{responseId}/input_items",
                    "description": "List preview response input items via Azure OpenAI"
                },
                {
                    "id": "azure-responses-v1-create",
                    "display_name": "Azure Responses API (v1)",
                    "method": "POST",
                    "url_template": "/v1/responses",
                    "description": "Create a response via Azure OpenAI v1 responses API"
                },
                {
                    "id": "azure-responses-v1-get",
                    "display_name": "Azure Retrieve Response (v1)",
                    "method": "GET",
                    "url_template": "/v1/responses/{responseId}",
                    "description": "Retrieve a response via Azure OpenAI v1 responses API"
                },
                {
                    "id": "azure-responses-v1-delete",
                    "display_name": "Azure Delete Response (v1)",
                    "method": "DELETE",
                    "url_template": "/v1/responses/{responseId}",
                    "description": "Delete a stored response via Azure OpenAI v1 responses API"
                },
                {
                    "id": "azure-responses-v1-input-items",
                    "display_name": "Azure Response Input Items (v1)",
                    "method": "GET",
                    "url_template": "/v1/responses/{responseId}/input_items",
                    "description": "List v1 response input items via Azure OpenAI"
                },
                {
                    "id": "azure-models-v1",
                    "display_name": "Azure Models (v1)",
                    "method": "GET",
                    "url_template": "/v1/models",
                    "description": "Access the Azure OpenAI v1 models registry"
                },
            ])

        operations.append({
            "id": "wildcard-post",
            "display_name": "Wildcard POST Operation",
            "method": "POST",
            "url_template": "/*",
            "description": "Matches unmatched POST requests"
        })
        return operations

    def _create_or_update_operation(self, rg_name, apim_name, api_id, operation):
        template_parameters = [
            ParameterContract(
                name=parameter_name,
                type="string",
                required=True,
                description=f"Path parameter '{parameter_name}'"
            )
            for parameter_name in re.findall(r"\{([^}]+)\}", operation['url_template'])
        ]

        try:
            self.apim_client.api_operation.create_or_update(
                rg_name,
                apim_name,
                api_id,
                operation['id'],
                parameters=OperationContract(
                    display_name=operation['display_name'],
                    method=operation['method'],
                    url_template=operation['url_template'],
                    template_parameters=template_parameters,
                    description=operation['description']
                )
            )
        except Exception as e:
            logger.warning(f"Failed to create/update op {operation['id']}: {e}")

    def create_resource_group(self):
        """创建或确认资源组"""
        rg_name = self.config['apim_resource_group']
        location = self.config['region']
        
        logger.info(f"Checking Resource Group '{rg_name}'...")
        if self.resource_client.resource_groups.check_existence(rg_name):
            logger.info(f"Resource Group '{rg_name}' already exists.")
        else:
            logger.info(f"Creating Resource Group '{rg_name}' in '{location}'...")
            self.resource_client.resource_groups.create_or_update(rg_name, {"location": location})
            logger.info(f"Resource Group created.")

    def create_managed_identity(self):
        """创建用户分配的托管身份 'aoai-nlb-identity'"""
        rg_name = self.config['apim_resource_group']
        location = self.config['region']
        
        logger.info(f"Checking Managed Identity '{IDENTITY_NAME}'...")
        try:
            identity = self.msi_client.user_assigned_identities.get(rg_name, IDENTITY_NAME)
            logger.info(f"Managed Identity '{IDENTITY_NAME}' already exists. Principal ID: {identity.principal_id}")
            self.identity_client_id = identity.client_id
            return identity
        except ResourceNotFoundError:
            logger.info(f"Creating Managed Identity '{IDENTITY_NAME}'...")
            identity = self.msi_client.user_assigned_identities.create_or_update(
                rg_name, 
                IDENTITY_NAME, 
                {"location": location}
            )
            logger.info(f"Managed Identity created. Principal ID: {identity.principal_id}")
            self.identity_client_id = identity.client_id
            return identity

    def assign_role_to_identity(self, identity):
        """为托管身份分配 Azure OpenAI 资源的 'Cognitive Services OpenAI User' 权限"""
        principal_id = identity.principal_id
        
        for aoai in self.config['azure-openai-list']:
            aoai_name = aoai['name']
            aoai_rg = aoai['resource_group']
            # 获取 AOAI 资源所属的 subscription ID
            sub_id = aoai.get('subscription_id', self.subscription_id)
            if not sub_id:
                logger.error(f"Cannot determine subscription ID for AOAI resource '{aoai_name}'. Skipping role assignment.")
                continue

            # 构造资源 Scope
            # 兼容两种 name 格式：纯名称 或 完整 Resource ID
            if '/' not in aoai_name:
                 scope = f"/subscriptions/{sub_id}/resourceGroups/{aoai_rg}/providers/Microsoft.CognitiveServices/accounts/{aoai_name}"
            else:
                 scope = aoai_name

            role_def_id = f"/subscriptions/{sub_id}/providers/Microsoft.Authorization/roleDefinitions/{OPENAI_USER_ROLE_ID}"
            
            # 使用基于 Scope+Principal+Role 的确定性 GUID
            # 避免使用 uuid.uuid4() 导致每次运行都尝试创建新 GUID 的赋值，从而依赖 Azure 去检测语义冲突
            # 确定性 GUID 能让重试逻辑更健壮
            role_assignment_seed = f"{scope}-{principal_id}-{OPENAI_USER_ROLE_ID}"
            role_assignment_name = str(uuid.uuid5(uuid.NAMESPACE_DNS, role_assignment_seed))

            # 如果目标订阅不同于当前 client 的订阅，需要创建一个临时的 auth_client
            # 虽然 role assignments 是基于 scope 的，但为了稳妥起见，针对目标订阅初始化 client
            if sub_id != self.subscription_id:
                logger.info(f"Target resource is in a different subscription ({sub_id}). Using specific Auth Client.")
                try:
                    auth_client_for_sub = AuthorizationManagementClient(self.credential, sub_id)
                except Exception as e:
                    logger.error(f"Failed to initialize Auth Client for subscription {sub_id}: {e}")
                    continue
            else:
                auth_client_for_sub = self.auth_client

            logger.info(f"Assigning 'Cognitive Services OpenAI User' role to {aoai_name}...")
            
            try:
                auth_client_for_sub.role_assignments.create(
                    scope,
                    role_assignment_name,
                    {
                        "role_definition_id": role_def_id,
                        "principal_id": principal_id,
                        "principal_type": "ServicePrincipal"
                    }
                )
                logger.info(f"Role assigned successfully for {aoai_name}.")
            except Exception as e:
                # 409 Conflict 通常意味着角色分配已存在
                if "RoleAssignmentExists" in str(e) or "Conflict" in str(e):
                    logger.info(f"Role assignment already exists for {aoai_name}.")
                else:
                    # 提升日志级别为 ERROR，因为如果分配失败，运行时一定会报 401
                    logger.error(f"CRITICAL: Could not assign role for {aoai_name} in scope {scope}.")
                    logger.error(f"Error Details: {e}")
                    logger.error("Please ensure your current credential (az login user or SP) has 'Owner' or 'User Access Administrator' permissions on the target subscription/resource group.")

    def create_apim_instance(self, identity):
        """创建或获取 APIM 实例 (Standard_v2 SKU)"""
        rg_name = self.config['apim_resource_group']
        apim_name = self.config['apim_name']
        location = self.config['region']
        
        logger.info(f"Checking APIM Instance '{apim_name}'...")
        
        # 修复 SDK 对于 StandardV2 SKU capacity 属性可能缺失的问题
        if 'capacity' not in ResourceSku._attribute_map:
             ResourceSku._attribute_map['capacity'] = {'key': 'capacity', 'type': 'int'}
             
        sku = ResourceSku(name="StandardV2", capacity=1)
        identity_prop = ApiManagementServiceIdentity(
            type="UserAssigned",
            user_assigned_identities={
                identity.id: UserIdentityProperties()
            }
        )
        
        params = ApiManagementServiceResource(
            location=location,
            sku=sku,
            publisher_email="admin@contoso.com",
            publisher_name="Contoso Admin",
            identity=identity_prop
        )

        try:
            apim = self.apim_client.api_management_service.get(rg_name, apim_name)
            logger.info(f"APIM Instance '{apim_name}' already exists. Using existing instance.")
            return apim
        except ResourceNotFoundError:
            pass

        logger.info(f"Creating APIM Instance '{apim_name}' (Standard V2)... Note: This operation can take 20-40 minutes.")
        
        poller = self.apim_client.api_management_service.begin_create_or_update(
            rg_name,
            apim_name,
            params
        )
        
        # 等待完成
        apim_instance = poller.result()
        logger.info(f"APIM Instance '{apim_name}' created successfully.")
        return apim_instance

    def configure_backends(self, apim_instance, identity):
        """配置 APIM Backends (Backend pool for Load Balancing)"""
        rg_name = self.config['apim_resource_group']
        apim_name = self.config['apim_name']
        
        backend_ids = []
        
        for idx, aoai in enumerate(self.config['azure-openai-list']):
            # 为每个 AOAI 资源创建一个 backend entity
            backend_id = f"aoai-backend-{idx}"
            # AOAI 标准 Endpoint 需要 /openai 前缀，例如 https://xxx.openai.azure.com/openai/deployments/...
            # 原始 endpoint 配置通常是 https://xxx.openai.azure.com/
            # 如果 API Path 定义为 /openai 且 APIM 剥离了它，我们需要在这里把 /openai 加回来
            # But for OpenAI mode with rewrite URI, we might want to control the full path.
            # However, to be consistent with normal AOAI passthrough, we keep /openai here.
            endpoint = aoai['endpoint'].rstrip('/') + "/openai"
            
            logger.info(f"Configuring Backend '{backend_id}' -> {endpoint}")
            
            try:
                self.apim_client.backend.get(rg_name, apim_name, backend_id)
                logger.info(f"Backend '{backend_id}' already exists. Skipping creation.")
                backend_ids.append(backend_id)
                continue
            except ResourceNotFoundError:
                pass

            backend_contract = BackendContract(
                description=f"Backend for {aoai['name']}",
                url=endpoint,
                protocol="http"
            )
            
            self.apim_client.backend.create_or_update(
                rg_name, apim_name, backend_id, backend_contract
            )
            backend_ids.append(backend_id)
            
        return backend_ids

    def create_load_balancing_policy_xml(self, backend_ids, is_openai_mode=False):
        """
        核心反向代理策略:
        1. 负载均衡: 使用缓存计数器执行 Round Robin
        2. 容错: 遇到 429/5xx 重试并切换 Backend
        3. 兼容性: (可选) 如果是 OpenAI 模式，重写 URI 和 Body
        """
        backend_selection_policy = self._build_backend_selection_policy(backend_ids)
        model_resolution_policy = self._build_model_resolution_policy()

        openai_compat_section = ""
        if is_openai_mode:
            openai_compat_section = f"""
        <!-- OpenAI Compatibility: rewrite friendly model aliases to Azure deployment routes -->
        <choose>
            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/chat/completions"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
                <rewrite-uri template='@("/deployments/" + (string)context.Variables["deploymentName"] + "/chat/completions")' />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>{AZURE_CHAT_API_VERSION}</value>
                </set-query-parameter>
            </when>

            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/embeddings"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
                <rewrite-uri template='@("/deployments/" + (string)context.Variables["deploymentName"] + "/embeddings")' />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>{AZURE_EMBEDDINGS_API_VERSION}</value>
                </set-query-parameter>
            </when>

            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/images/generations"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
                <rewrite-uri template='@("/deployments/" + (string)context.Variables["deploymentName"] + "/images/generations")' />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>{AZURE_IMAGES_API_VERSION}</value>
                </set-query-parameter>
            </when>

            <when condition='@(context.Request.Method == "POST" &amp;&amp; context.Request.OriginalUrl.Path.EndsWith("/responses"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
                <rewrite-uri template='@(context.Request.OriginalUrl.Path)' />
            </when>

            <when condition='@(context.Request.OriginalUrl.Path.Contains("/responses/") || (context.Request.Method == "GET" &amp;&amp; context.Request.OriginalUrl.Path.EndsWith("/models")))'>
                <rewrite-uri template='@(context.Request.OriginalUrl.Path)' />
            </when>
        </choose>
            """
        else:
            openai_compat_section = f"""
        <!-- Azure native responses compatibility: support preview /openai/responses and v1 /openai/v1/responses -->
        <choose>
            <when condition='@(context.Request.Method == "POST" &amp;&amp; context.Request.OriginalUrl.Path.EndsWith("/responses"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
                <set-query-parameter name="api-version" exists-action="override">
                    <value>{AZURE_RESPONSES_PREVIEW_API_VERSION}</value>
                </set-query-parameter>
            </when>

            <when condition='@(context.Request.OriginalUrl.Path.Contains("/responses/") &amp;&amp; !context.Request.OriginalUrl.Path.Contains("/v1/responses/"))'>
                <set-query-parameter name="api-version" exists-action="override">
                    <value>{AZURE_RESPONSES_PREVIEW_API_VERSION}</value>
                </set-query-parameter>
            </when>

            <when condition='@(context.Request.Method == "POST" &amp;&amp; context.Request.OriginalUrl.Path.Contains("/v1/responses"))'>
                <set-variable name="requestModel" value='@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    return requestBody?["model"]?.ToString() ?? string.Empty;
                }}' />
                <choose>
                    <when condition='@(string.IsNullOrEmpty((string)context.Variables["requestModel"]))'>
                        <return-response>
                            <set-status code="400" reason="Bad Request" />
                            <set-header name="Content-Type" exists-action="override">
                                <value>application/json</value>
                            </set-header>
                            <set-body>{{"error": {{"message": "Request body must include a non-empty model field."}}}}</set-body>
                        </return-response>
                    </when>
                </choose>
                {model_resolution_policy}
                <set-body>@{{
                    var requestBody = context.Request.Body.As&lt;JObject&gt;(preserveContent: true);
                    requestBody["model"] = (string)context.Variables["deploymentName"];
                    return requestBody.ToString();
                }}</set-body>
            </when>
        </choose>
            """

        policy_xml = f"""
<policies>
    <inbound>
        <base />
        {openai_compat_section}

        <!-- REMOVE API KEY TO AVOID BACKEND AUTH CONFUSION -->
        <set-header name="api-key" exists-action="delete" />
        
        <!-- Authenticate with Backend using Managed Identity -->
        <authentication-managed-identity resource="https://cognitiveservices.azure.com" output-token-variable-name="msi-access-token" ignore-error="false" client-id="{self.identity_client_id}" />
        <set-header name="Authorization" exists-action="override">
            <value>@("Bearer " + (string)context.Variables["msi-access-token"])</value>
        </set-header>

        <!-- Round Robin selection backed by APIM cache -->
        <cache-lookup-value key="{ROUND_ROBIN_CACHE_KEY}" variable-name="cachedBackendIndex" />
        <set-variable name="backendIndex" value='@{{
            var cachedValue = context.Variables.ContainsKey("cachedBackendIndex") ? (string)context.Variables["cachedBackendIndex"] : "0";
            int currentIndex = 0;
            if (!int.TryParse(cachedValue, out currentIndex))
            {{
                currentIndex = 0;
            }}
            return ((currentIndex % {len(backend_ids)}) + {len(backend_ids)}) % {len(backend_ids)};
        }}' />
        <cache-store-value key="{ROUND_ROBIN_CACHE_KEY}" value='@((((int)context.Variables["backendIndex"] + 1) % {len(backend_ids)}).ToString())' duration="86400" />
        {backend_selection_policy}
        <set-backend-service backend-id='@((string)context.Variables["selectedBackend"])' />
    </inbound>
    <backend>
        <!-- Retry Policy for 429, 500, and 503 -->
        <retry condition="@(context.Response.StatusCode == 429 || context.Response.StatusCode == 500 || context.Response.StatusCode == 503)" count="2" interval="0" first-fast-retry="true">
            <!-- 轮询切换到下一个 Backend -->
            <set-variable name="backendIndex" value='@(((int)context.Variables["backendIndex"] + 1) % {len(backend_ids)})' />
            {backend_selection_policy}
            <set-backend-service backend-id='@((string)context.Variables["selectedBackend"])' />
            <!-- Increased timeout to 120s for DALL-E and Reasoning models -->
            <forward-request buffer-request-body="true" timeout="120" />
        </retry>
    </backend>
    <outbound>
        <base />
        <!-- 日志记录建议使用 Azure Monitor，但也可用 Trace 辅助调试 -->
        <trace source="AOAI-LB" severity="information">
            <message>@("Backend: " + (string)context.Variables["selectedBackend"] + " | Status: " + ((IResponse)context.Response).StatusCode.ToString())</message>
        </trace>
    </outbound>
    <on-error>
        <base />
    </on-error>
</policies>
"""
        return policy_xml

    def create_api(self, backend_ids):
        rg_name = self.config['apim_resource_group']
        apim_name = self.config['apim_name']
        
        # 定义两个 API 配置：原生 Azure 模式 和 OpenAI 兼容模式
        api_configs = [
            {
                "id": "azure-openai-api",
                "path": "openai", 
                "display_name": "Azure OpenAI Native API",
                "desc": "Standard Azure OpenAI API (prefix /openai)",
                "mode": "aoai"
            },
            {
                "id": "openai-compatible-api",
                "path": "v1",
                "display_name": "OpenAI Compatible API",
                "desc": "Standard OpenAI API (prefix /v1)",
                "mode": "openai"
            }
        ]

        created_apis = []

        for api_cfg in api_configs:
            api_id = api_cfg["id"]
            api_path = api_cfg["path"]
            display_name = api_cfg["display_name"]
            mode = api_cfg["mode"]
            is_openai_mode = (mode == "openai")

            logger.info(f"Creating API '{display_name}' at path '/{api_path}'...")
            
            # Check if API exists and delete if so (Force Update)
            try:
                self.apim_client.api.get(rg_name, apim_name, api_id)
                logger.info(f"API '{api_id}' exists. Deleting for force update...")
                # Use begin_delete for modern SDK consistency
                try:
                    self.apim_client.api.begin_delete(rg_name, apim_name, api_id, if_match="*").result()
                except AttributeError:
                    # Fallback for older SDKs if begin_delete is missing but delete exists (though previous error suggests delete missing)
                    self.apim_client.api.delete(rg_name, apim_name, api_id, if_match="*")
                
                logger.info(f"API '{api_id}' deleted.")
            except ResourceNotFoundError:
                pass
            
            api_params = ApiCreateOrUpdateParameter(
                display_name=display_name,
                path=api_path,
                protocols=["https"],
                subscription_required=True,
                api_type="http",
                description=api_cfg["desc"],
                subscription_key_parameter_names=SubscriptionKeyParameterNamesContract(
                    header="api-key",
                    query="api-key"
                )
            )
            
            self.apim_client.api.begin_create_or_update(
                rg_name, apim_name, api_id, api_params
            ).result()
            
            # 应用负载均衡 Policy
            # 传入 is_openai_mode 参数以决定是否注入重写逻辑
            policy_xml = self.create_load_balancing_policy_xml(backend_ids, is_openai_mode=is_openai_mode)
            self.apim_client.api_policy.create_or_update(
                rg_name, apim_name, api_id, 
                policy_id="policy", 
                parameters=PolicyContract(value=policy_xml, format="xml")
            )
            
            logger.info(f"Configuring operations for {api_id}...")

            for operation in self._build_api_operations(is_openai_mode=is_openai_mode):
                self._create_or_update_operation(rg_name, apim_name, api_id, operation)

            created_apis.append(api_path)

        logger.info("All APIs configured successfully.")
        return created_apis


    def run(self):
        logger.info(">>> Starting AOAI Load Balancer Deployment <<<")
        
        # 1. Ensure Resource Group Exists
        self.create_resource_group()
        
        # 2. Ensure Managed Identity Exists
        identity = self.create_managed_identity()
        self.identity_client_id = identity.client_id
         
        # 3. Assign Roles (optional, but recommended if running with sufficient permissions)
        # Note: This requires the current user to have Owner/User Access Administrator on the target scope
        try:
            self.assign_role_to_identity(identity)
        except Exception as e:
            logger.warning(f"Failed to assign roles. You might need to do this manually. Error: {e}")
        
        # 4. Create/Update APIM Instance
        apim = self.create_apim_instance(identity)
        gateway_url = apim.gateway_url
        
        # 5. Configure Backends
        backend_ids = self.configure_backends(apim, identity)
        
        # 6. Create APIs (Azure Mode + OpenAI Mode)
        created_paths = self.create_api(backend_ids)
        
        logger.info(">>> Deployment Complete <<<")
        logger.info(f"Target Gateway URL: {gateway_url}")
        logger.info("Deployed APIs:")
        for path in created_paths:
            logger.info(f"  - /{path} (e.g. {gateway_url}/{path}/...)")

        logger.info("Example endpoints:")
        logger.info(f"  - OpenAI Chat: {gateway_url}/v1/chat/completions")
        logger.info(f"  - OpenAI Embeddings: {gateway_url}/v1/embeddings")
        logger.info(f"  - OpenAI Images: {gateway_url}/v1/images/generations")
        logger.info(f"  - OpenAI Responses: {gateway_url}/v1/responses")
        logger.info(f"  - Azure Chat: {gateway_url}/openai/deployments/<deployment>/chat/completions?api-version={AZURE_CHAT_API_VERSION}")
        logger.info(f"  - Azure Images: {gateway_url}/openai/deployments/<deployment>/images/generations?api-version={AZURE_IMAGES_API_VERSION}")
        logger.info(f"  - Azure Responses (preview): {gateway_url}/openai/responses?api-version={AZURE_RESPONSES_PREVIEW_API_VERSION}")
        logger.info(f"  - Azure Responses (v1): {gateway_url}/openai/v1/responses")
            
        logger.info(f"Managed Identity Client ID: {identity.client_id}")
        
        # 尝试检索 Subscription Key
        try:
             # 获取 'master' subscription 的 keys
             keys = self.apim_client.subscription.list_secrets(
                 self.config['apim_resource_group'], self.config['apim_name'], "master"
             )
             logger.info(f"IMPORTANT: Primary Subscription Key: {keys.primary_key}")
        except Exception as e:
             logger.warning(f"Could not retrieve subscription key: {e}")
             logger.info("IMPORTANT: To use, get a Subscription Key from APIM Portal or creating one via CLI.")

if __name__ == "__main__":
    if "AZURE_SUBSCRIPTION_ID" not in os.environ:
         print("Suggestion: Set AZURE_SUBSCRIPTION_ID environment variable for explicit context.")
    
    manager = AzureDeploymentManager()
    manager.run()
