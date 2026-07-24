# LiteLLM 修复镜像拉取问题解决方案

## 背景与问题本质

本项目部署默认使用的镜像是 **`micl/litellm:mi-fix-image-gen`**（Docker Hub 上的私有修复版，基于 litellm `1.81.16` 从源码 fork 构建）。

镜像默认值在 [`deploy_mi_aks_litellm.py`](deploy_mi_aks_litellm.py) 中，可用环境变量 `LITELLM_IMAGE` 覆盖：

```python
"litellm_image": os.environ.get("LITELLM_IMAGE", "micl/litellm:mi-fix-image-gen"),
```

这个修复版专门绕开了 **Codex Desktop 走 Responses API（`wire_api = "responses"`）时 Azure OpenAI 返回的 `image_gen` 命名空间冲突**：

```text
litellm.BadRequestError: AzureException BadRequestError - {
  "error": {
    "message": "Invalid Value: 'tools.namespace'. User-defined namespace 'image_gen' collides with an existing tool namespace.",
    "type": "invalid_request_error",
    "param": "tools.namespace",
    "code": null
  }
}. Received Model Group=gpt-5.6-sol
```

> **关键结论：官方镜像没有这个补丁。** 把镜像换成官方 `ghcr.io/berriai/litellm` 会复现上面的 `BadRequestError`。
> 因此不能换官方镜像 —— 必须让客户能拉取这个**修复镜像**。问题从“要不要换镜像”变成了“**如何把修复镜像送进客户能拉取的仓库**”。

### 客户拉不到镜像的常见原因

- 企业网络禁止访问 Docker Hub
- 只允许白名单/受信任的镜像仓库
- `micl` 这类个人 namespace 不被安全策略信任
- 集群无公网出口

---

## 方案 A（首选）：用 `az acr import` 镜像进客户自己的 ACR

`az acr import` 在 **Azure 骨干网服务端**去 Docker Hub 拉取再写入客户 ACR，**不经过客户的受限网络出口**，所以即使客户集群/网络禁了 Docker Hub 也能成功。

### 步骤 1：创建 ACR（一次性，客户订阅里）

```powershell
$rg   = "<客户RG>"
$acr  = "<全局唯一的ACR名>"      # 只能小写字母+数字，如 litellmacr2401
$loc  = "<区域>"                  # 例如 eastus2，建议与 AKS 同区域

az acr create -g $rg -n $acr --sku Basic -l $loc
```

- SKU 选 **`Basic`** 即可（只放一个镜像，最便宜，10GB 容量）。
- ACR 名需**全局唯一**、只能小写字母数字、5–50 字符。
- 登录服务器地址为 `<acr>.azurecr.io`。

> **可能遇到的报错：`MissingSubscriptionRegistration`**
>
> ```text
> (MissingSubscriptionRegistration) The subscription is not registered to use
> namespace 'Microsoft.ContainerRegistry'.
> ```
>
> 说明订阅还没注册 ACR 的资源提供程序（订阅级一次性操作）。先注册再重建：
>
> ```powershell
> # 触发注册（异步，通常 1–2 分钟）
> az provider register --namespace Microsoft.ContainerRegistry
>
> # 轮询直到变成 Registered
> do {
>   Start-Sleep 15
>   $state = az provider show -n Microsoft.ContainerRegistry --query "registrationState" -o tsv
>   "state: $state"
> } while ($state -ne "Registered")
>
> # 注册完成后重跑创建
> az acr create -g $rg -n $acr --sku Basic -l $loc
> ```

### 步骤 2：服务端导入镜像

```powershell
az acr import `
  --name $acr `
  --source docker.io/micl/litellm:mi-fix-image-gen `
  --image litellm:mi-fix-image-gen
```

### 步骤 3：给 AKS 授 AcrPull 权限

```powershell
az aks update -g <客户RG> -n <客户AKS> --attach-acr $acr
```

`--attach-acr` 会自动给 AKS 的 kubelet 托管身份授 **AcrPull** 角色，无需手动配置 `imagePullSecret`。

### 步骤 4：用新镜像地址重新部署

```powershell
$env:LITELLM_IMAGE = "$acr.azurecr.io/litellm:mi-fix-image-gen"
python LiteLLM/deploy_mi_aks_litellm.py
```

> `$rg`、`$acr` 这类**普通 PowerShell 变量**用于当前会话里的 `az` 命令即可；唯独最后镜像地址要用 `$env:LITELLM_IMAGE`，因为它需要被 python **子进程**继承。以上命令请在**同一个 pwsh 窗口**里连续执行。

---

## 方案 B：从能联网的机器中转推送

如果连 `az acr import` 都被策略拦截（极少见），在**能拉取 Docker Hub 的机器**上中转：

```powershell
docker pull docker.io/micl/litellm:mi-fix-image-gen
docker tag  docker.io/micl/litellm:mi-fix-image-gen $acr.azurecr.io/litellm:mi-fix-image-gen
az acr login -n $acr
docker push $acr.azurecr.io/litellm:mi-fix-image-gen
```

**完全离线**场景：

```powershell
docker save docker.io/micl/litellm:mi-fix-image-gen -o litellm-fix.tar
# 通过 U盘/内网把 litellm-fix.tar 传给客户，然后在客户环境：
docker load -i litellm-fix.tar
docker tag  docker.io/micl/litellm:mi-fix-image-gen <客户registry>/litellm:mi-fix-image-gen
docker push <客户registry>/litellm:mi-fix-image-gen
```

同样适用于客户内网自建仓库（Harbor 等）。

---

## 方案 C（长期解）：在官方镜像上重建补丁

彻底摆脱对 `micl` 这个不透明私有镜像的依赖 —— 用一个基于官方镜像的 `Dockerfile` 把修复重新打上去，镜像放客户自己的 registry，以后自行维护：

```dockerfile
FROM ghcr.io/berriai/litellm:v1.81.16
# 应用 image_gen 命名空间冲突修复补丁
# COPY patched_file.py /usr/lib/python3.13/site-packages/litellm/...
```

前提是拿到确切补丁 diff。修复并非简单的文件级 patch（整个 litellm 是从 fork 源码重建，无法用文件修改时间区分），需要将修复镜像内的 litellm 源码与官方 `pip` 版 `1.81.16` 逐文件 diff 才能精确还原。

---

## 方案 D（最不推荐）：imagePullSecrets

仅当拦截只是 Docker Hub 匿名限速/需要登录（而非硬性网络封锁）时才有用 —— 添加一个带 Docker Hub 凭据的 pull secret。对企业策略封锁无效，不建议作为主方案。

---

## 方案对比

| 方案 | 适用场景 | 推荐度 |
|---|---|---|
| **A. `az acr import` 进客户 ACR** | 客户有/可建 ACR，网络禁 Docker Hub | ⭐⭐⭐⭐⭐ 首选 |
| **B. 中转/离线推送** | 连 `acr import` 都被拦，或完全离线 | ⭐⭐⭐⭐ |
| **C. 官方镜像重建补丁** | 想彻底自给自足、长期维护 | ⭐⭐⭐ 长期解 |
| **D. imagePullSecrets** | 仅 Docker Hub 限速/登录问题 | ⭐ 兜底 |

---

## 一键脚本（方案 A 完整流程）

填好前 4 个变量即可整段运行：

```powershell
# ==== 按客户环境填写 ====
$sub  = "<客户订阅ID>"
$rg   = "<客户RG>"
$aks  = "<客户AKS名>"
$acr  = "<全局唯一ACR名>"       # 小写字母+数字
$loc  = "<区域>"                 # 建议与 AKS 同区域
# ========================

az account set --subscription $sub

# 1. 创建 ACR（已存在会报错，可忽略或跳过）
# 若报 MissingSubscriptionRegistration，先注册资源提供程序：
az provider register --namespace Microsoft.ContainerRegistry
do {
  Start-Sleep 15
  $state = az provider show -n Microsoft.ContainerRegistry --query "registrationState" -o tsv
  "provider: $state"
} while ($state -ne "Registered")

az acr create -g $rg -n $acr --sku Basic -l $loc

# 2. 服务端导入修复镜像
az acr import --name $acr `
  --source docker.io/micl/litellm:mi-fix-image-gen `
  --image litellm:mi-fix-image-gen

# 3. 授权 AKS 拉取
az aks update -g $rg -n $aks --attach-acr $acr

# 4. 用新镜像重新部署
$env:LITELLM_IMAGE = "$acr.azurecr.io/litellm:mi-fix-image-gen"
python LiteLLM/deploy_mi_aks_litellm.py
```
