param(
    [switch]$PersistToUser
)

# Generate a strong LiteLLM master key in the format: sk-<base64url>
$bytes = New-Object byte[] 48
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$masterKey = "sk-$token"

# Set for current PowerShell session
$env:LITELLM_MASTER_KEY = $masterKey

# Optionally persist to user-level environment variables
if ($PersistToUser) {
    [System.Environment]::SetEnvironmentVariable("LITELLM_MASTER_KEY", $masterKey, "User")
}

Write-Host "LITELLM_MASTER_KEY generated and set for current session."
if ($PersistToUser) {
    Write-Host "Also persisted to user environment variables."
}

Write-Host "Value: $masterKey"
Write-Host "Length: $($masterKey.Length)"

if ($masterKey.Length -lt 24) {
    throw "Generated key is too short."
}
