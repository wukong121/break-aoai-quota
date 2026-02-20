import os
import sys
import json
import time
import logging
import uuid
import random
from azure.identity import DefaultAzureCredential, InteractiveBrowserCredential
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError, HttpResponseError
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.msi import ManagedServiceIdentityClient
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.apimanagement import ApiManagementClient
from azure.mgmt.apimanagement.models import (
    ApiManagementServiceResource,
    ApiCreateOrUpdateParameter,
    BackendContract,
    BackendCredentialsContract,
    BackendProperties,
    PolicyContract,
    ApiManagementServiceIdentity,
    UserIdentityProperties,
    ResourceSku,
    OperationContract,
    SubscriptionKeyParameterNamesContract
)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 常量定义
# "Cognitive Services OpenAI User" Role ID
OPENAI_USER_ROLE_ID = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
IDENTITY_NAME = "aoai-nlb-identity"

class AzureDeploymentManager:
    def __init__(self, config_file="azure-openai.json"):
        self.config = self._load_config(config_file)
        
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
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config file: {e}")
            raise

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
        client_id = identity.client_id
        
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
        1. 负载均衡: 随机选择 Backend
        2. 容错: 遇到 429/5xx 重试并切换 Backend
        3. 兼容性: (可选) 如果是 OpenAI 模式，重写 URI 和 Body
        """
        # 生成 C# 数组初始化逻辑
        backend_add_statements = ""
        for bid in backend_ids:
            backend_add_statements += f'backends.Add("{bid}");\n            '
        
        # 兼容性逻辑：如果是 OpenAI 模式，针对 /chat/completions 进行重写
        openai_compat_section = ""
        if is_openai_mode:
            # Note: Using single quotes for XML attribute values to allow double quotes in C# expressions
            # Also escaping < and > to &lt; and &gt; for XML compliance inside attributes
            openai_compat_section = """
        <!-- OpenAI Compatibility: Rewrite URI and Body for Chat & Image Completions -->
        <choose>
            <!-- Chat Completions -->
            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/chat/completions"))'>
                <set-variable name="requestBody" value='@(context.Request.Body.As&lt;JObject&gt;(preserveContent: true))' />
                <set-variable name="model" value='@((string)((JObject)context.Variables["requestBody"])["model"])' />
                
                <!-- Rewrite URI to Azure Format: /deployments/{model}/chat/completions?api-version=2024-02-15-preview -->
                <rewrite-uri template='@("/deployments/" + (string)context.Variables["model"] + "/chat/completions")' />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>2024-02-15-preview</value>
                </set-query-parameter>
            </when>
            
            <!-- Image Generations (DALL-E) -->
            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/images/generations"))'>
                <set-variable name="requestBody" value='@(context.Request.Body.As&lt;JObject&gt;(preserveContent: true))' />
                <set-variable name="model" value='@((string)((JObject)context.Variables["requestBody"])["model"])' />
                
                <!-- Rewrite URI to Azure Format: /deployments/{model}/images/generations?api-version=2024-02-01 -->
                <rewrite-uri template='@("/deployments/" + (string)context.Variables["model"] + "/images/generations")' />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>2024-02-01</value>
                </set-query-parameter>
            </when>

            <!-- Responses API (Corrected per user requirement) -->
            <when condition='@(context.Request.OriginalUrl.Path.EndsWith("/responses"))'>
                <set-variable name="requestBody" value='@(context.Request.Body.As&lt;JObject&gt;(preserveContent: true))' />
                <set-variable name="model" value='@((string)((JObject)context.Variables["requestBody"])["model"])' />
                
                <!-- Rewrite URI to Azure Format for Responses: /openai/responses?api-version=2025-01-01-preview -->
                <!-- Assuming the backend is already pointing to /openai, we just need /responses -->
                <!-- But wait, if backend includes /openai prefix (as per configure_backends method) -->
                <!-- The backend URL is defined as: endpoint.rstrip('/') + "/openai" -->
                <!-- So if APIM appends the path from request... -->
                <!-- If request is /v1/responses (OpenAI mode), APIM might append /responses? -->
                <!-- Actually, we are rewriting URI here explicitly. -->
                
                <!-- Target: {backend}/responses?api-version=... -->
                <rewrite-uri template="/responses" />
                <set-query-parameter name="api-version" exists-action="override">
                    <value>2025-04-01-preview</value>
                </set-query-parameter>
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

        <!-- Load Balancing Configuration -->
        <!-- 定义所有可用后端 -->
        <set-variable name="backends" value='@{{
            JArray backends = new JArray();
            {backend_add_statements}
            return backends;
        }}' />
        
        <!-- 随机选择初始 Backend -->
        <set-variable name="backendIndex" value="@(new Random().Next(0, {len(backend_ids)}))" />
        <set-variable name="selectedBackend" value='@((string)((JArray)context.Variables["backends"])[(int)context.Variables["backendIndex"]])' />
        
        <!-- 应用 Backend -->
        <set-backend-service backend-id='@((string)context.Variables["selectedBackend"])' />
    </inbound>
    <backend>
        <!-- Retry Policy for 429 and 5xx -->
        <retry condition="@(context.Response.StatusCode == 429 || context.Response.StatusCode >= 500)" count="2" interval="0" first-fast-retry="true">
            <!-- 轮询切换到下一个 Backend -->
            <set-variable name="backendIndex" value='@(((int)context.Variables["backendIndex"] + 1) % {len(backend_ids)})' />
            <set-variable name="selectedBackend" value='@((string)((JArray)context.Variables["backends"])[(int)context.Variables["backendIndex"]])' />
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
            
            # 创建 Operations
            logger.info(f"Configuring operations for {api_id}...")
            
            # 1. 针对 deployment_list 创建 Operation (/deployments/{name}/...)
            #    这对 Azure 模式是必需的。对 OpenAI 模式，虽然主要用 rewrite，但保留也不冲突，或者可以只为 Azure 模式创建。
            #    为了简单兼容，我们在两种模式下都创建标准路径，OpenAI 模式额外通过 Policy 重写 /chat/completions。
            
            for dep in self.config['deployment_list']:
                model_name = dep['model']
                dep_name = dep['deployment_name']
                
                # Determine operation type based on model name
                if "image" in model_name.lower() or "dall-e" in model_name.lower():
                    # Image Generation
                    op_id = f"image-{dep_name}".replace(".", "-").replace(" ", "-")
                    url_template = f"/deployments/{dep_name}/images/generations"
                    display_name = f"Image Generation ({dep_name})"
                    method = "POST"
                elif "embedding" in model_name.lower():
                    # Embeddings
                    op_id = f"embed-{dep_name}".replace(".", "-").replace(" ", "-")
                    url_template = f"/deployments/{dep_name}/embeddings"
                    display_name = f"Embeddings ({dep_name})"
                    method = "POST"
                else:
                    # Default to Chat Completion
                    # Use 'chat-' prefix to match previous deployment and update in-place
                    op_id = f"chat-{dep_name}".replace(".", "-").replace(" ", "-")
                    url_template = f"/deployments/{dep_name}/chat/completions"
                    display_name = f"Chat Completion ({dep_name})"
                    method = "POST"
                
                try:
                    self.apim_client.api_operation.create_or_update(
                        rg_name, apim_name, api_id, op_id,
                        parameters=OperationContract(
                            display_name=display_name,
                            method=method,
                            url_template=url_template,
                            description=f"Proxy for model {model_name}"
                        )
                    )
                    
                except Exception as e:
                    logger.warning(f"Failed to create/update op {op_id}: {e}")
                    # If failure is due to conflict (e.g. changing type), try deleting first?
                    # But usually ID stability prevents this unless URL changed for same ID.
                    pass

            # 2. 如果是 OpenAI 模式，显式添加 Standard Operations
            if is_openai_mode:
                # Chat Completions
                self.apim_client.api_operation.create_or_update(
                    rg_name, apim_name, api_id, "openai-chat-completions",
                    parameters=OperationContract(
                        display_name="OpenAI Chat Completions",
                        method="POST",
                        url_template="/chat/completions",
                        description="Access chat models via 'model' body parameter"
                    )
                )
                
                # Image Generations
                self.apim_client.api_operation.create_or_update(
                    rg_name, apim_name, api_id, "openai-images-generations",
                    parameters=OperationContract(
                        display_name="OpenAI Image Generations",
                        method="POST",
                        url_template="/images/generations",
                        description="Access image models via 'model' body parameter"
                    )
                )

                # Responses
                self.apim_client.api_operation.create_or_update(
                    rg_name, apim_name, api_id, "openai-responses",
                    parameters=OperationContract(
                        display_name="OpenAI Responses",
                        method="POST",
                        url_template="/responses",
                        description="Access response models via 'model' body parameter"
                    )
                )
            
            # 3. Wildcard Operation
            self.apim_client.api_operation.create_or_update(
                rg_name, apim_name, api_id, "wildcard-all",
                parameters=OperationContract(
                    display_name="Wildcard Operation",
                    method="POST",
                    url_template="/*",
                    description="Matches all other requests"
                )
            )
            
            # 4. Global Responses Operation (Native Azure)
            # Only create this global operation if we are NOT in OpenAI mode (to avoid conflict with openai-responses)
            if not is_openai_mode:
                try:
                    self.apim_client.api_operation.create_or_update(
                        rg_name, apim_name, api_id, "global-responses",
                        parameters=OperationContract(
                            display_name="Global Responses API",
                            method="POST",
                            url_template="/responses",
                            description="Access Responses API (global)"
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to create global responses op: {e}")

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
