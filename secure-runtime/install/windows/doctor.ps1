[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:ProgramData\MulticaWorkflow",
    [string]$ServiceName = "MulticaWorkflowSecureRuntime"
)

$ErrorActionPreference = 'Stop'
$runtimePath = Join-Path $InstallRoot 'runtime.local.json'
if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) {
    throw "Secure runtime config is missing: $runtimePath"
}
$runtime = Get-Content -Raw -LiteralPath $runtimePath | ConvertFrom-Json
$checks = [ordered]@{}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-NoBroadAcl([string]$Path) {
    $broad = @('S-1-1-0','S-1-5-11','S-1-5-32-545')
    foreach ($rule in (Get-Acl -LiteralPath $Path).Access) {
        try {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } catch {
            return $false
        }
        if ($broad -contains $sid -and $rule.AccessControlType -eq 'Allow') {
            return $false
        }
    }
    return $true
}

foreach ($name in @('multica_executable','codex_executable','git_executable','launcher_executable','broker_executable','console_executable')) {
    $entry = $runtime.$name
    $observed = (Get-FileHash -LiteralPath $entry.path -Algorithm SHA256).Hash.ToLowerInvariant()
    $checks["hash.$name"] = $observed -eq $entry.sha256
}
$checks['credential.codex'] = Test-Path -LiteralPath $runtime.codex_credential_path -PathType Leaf
$checks['credential.app'] = Test-Path -LiteralPath $runtime.github.private_key_path -PathType Leaf
$checks['capability.key'] = Test-Path -LiteralPath $runtime.capability_key_path -PathType Leaf
$checks['agent.bindings'] = Test-Path -LiteralPath $runtime.agent_bindings_path -PathType Leaf
$checks['bundle.manifest'] = Test-Path -LiteralPath $runtime.workflow_bundle_manifest -PathType Leaf
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
$checks['service.exists'] = $null -ne $service
$checks['service.account'] = $null -ne $service -and $service.StartName -eq "NT SERVICE\$ServiceName"
$checks['service.running'] = $null -ne $service -and $service.State -eq 'Running'
$checks['firewall.codex'] = $null -ne (Get-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Codex direct egress block' -ErrorAction SilentlyContinue)
$checks['firewall.multica'] = $null -ne (Get-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Multica direct egress block' -ErrorAction SilentlyContinue)

$bindings = if ($checks['agent.bindings']) { Get-Content -Raw -LiteralPath $runtime.agent_bindings_path | ConvertFrom-Json } else { $null }
$bindingRuntime = [Guid]::Empty
$checks['agent.bindings.runtime'] = $null -ne $bindings -and
    [Guid]::TryParse([string]$bindings.runtime_id, [ref]$bindingRuntime) -and
    $bindingRuntime -ne [Guid]::Empty -and
    @($bindings.agents.PSObject.Properties).Count -gt 0
$checks['agent.bindings.final'] = $null -ne $bindings -and $bindings.bootstrap -ne $true

$bundleHashesValid = $false
if ($checks['bundle.manifest']) {
    $manifest = Get-Content -Raw -LiteralPath $runtime.workflow_bundle_manifest | ConvertFrom-Json
    $bundleRoot = [IO.Path]::GetFullPath([string]$runtime.workflow_bundle_root).TrimEnd('\') + '\'
    $bundleHashesValid = $manifest.schema_version -eq 1
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $relative = [string]$entry.Name
        $candidate = [IO.Path]::GetFullPath((Join-Path $runtime.workflow_bundle_root ($relative -replace '/', '\')))
        if (-not $candidate.StartsWith($bundleRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
            (Get-Sha256 -Path $candidate) -ne ([string]$entry.Value).ToLowerInvariant()) {
            $bundleHashesValid = $false
            break
        }
    }
}
$checks['bundle.hashes'] = $bundleHashesValid
$checks['acl.credentials'] = Test-NoBroadAcl (Join-Path $InstallRoot 'credentials')
$checks['acl.config'] = Test-NoBroadAcl (Join-Path $InstallRoot 'config')
$checks['acl.service_home'] = Test-NoBroadAcl (Join-Path $InstallRoot 'service-home')
$checks['acl.runtime_config'] = Test-NoBroadAcl $runtimePath
$taskRoot = Join-Path $InstallRoot 'tasks'
$checks['tasks.ephemeral'] = -not (Test-Path -LiteralPath $taskRoot) -or
    @(Get-ChildItem -LiteralPath $taskRoot -Force -ErrorAction SilentlyContinue).Count -eq 0

$env:MULTICA_SECURE_CONFIG = $runtimePath
$probe = & $runtime.launcher_executable.path --version 2>&1
$checks['launcher.probe'] = $LASTEXITCODE -eq 0 -and ($probe -match 'codex')
foreach ($role in @('workflow_maintainer','workflow_reviewer')) {
    $profileProbe = & $runtime.launcher_executable.path --workflow-security-probe $role 2>&1
    $checks["launcher.profile.$role"] = $LASTEXITCODE -eq 0 -and ($profileProbe -match 'codex')
}

$failed = @($checks.GetEnumerator() | Where-Object {-not $_.Value} | ForEach-Object {$_.Key})
[ordered]@{
    status = if ($failed.Count -eq 0) {'ready'} else {'blocked'}
    checks = $checks
    failed = $failed
    note = 'This doctor is local and read-only. Host gh/SSH credentials are intentionally irrelevant because the service and Agent environments are isolated.'
} | ConvertTo-Json -Depth 6
if ($failed.Count -gt 0) { exit 1 }
