[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$ArtifactDirectory,
    [Parameter(Mandatory)][string]$WorkflowBundleRoot,
    [Parameter(Mandatory)][string]$MulticaExecutable,
    [Parameter(Mandatory)][string]$CodexExecutable,
    [Parameter(Mandatory)][string]$GitExecutable,
    [Parameter(Mandatory)][string]$MulticaProfileDirectory,
    [Parameter(Mandatory)][string]$CodexAuthJson,
    [Parameter(Mandatory)][string]$MaintainerAppPrivateKey,
    [Parameter(Mandatory)][long]$MaintainerAppId,
    [Parameter(Mandatory)][long]$MaintainerInstallationId,
    [Parameter(Mandatory)][string]$AgentBindings,
    [string]$InstallRoot = "$env:ProgramData\MulticaWorkflow",
    [string]$ServiceName = "MulticaWorkflowSecureRuntime",
    [string]$MulticaProfile = "workflow-secure",
    [string]$DaemonId = "workflow-secure-daemon",
    [string]$RuntimeName = "Workflow Secure Codex"
)

$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Secure runtime installation requires an elevated PowerShell session.'
    }
}

function Resolve-RequiredFile([string]$Path, [string]$Label) {
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label is not a file: $resolved"
    }
    return $resolved
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Copy-DirectoryContents([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

function Reset-DirectoryUnder([string]$Parent, [string]$Target) {
    $parentPath = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    $targetPath = [IO.Path]::GetFullPath($Target).TrimEnd('\')
    if (-not $targetPath.StartsWith($parentPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset a directory outside $Parent`: $Target"
    }
    if (Test-Path -LiteralPath $targetPath) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $targetPath | Out-Null
}

function Set-SecureFirewallRules(
    [string]$CodexPath,
    [string]$MulticaPath,
    [string]$ServiceAccount
) {
    $sid = ([Security.Principal.NTAccount]$ServiceAccount).Translate([Security.Principal.SecurityIdentifier]).Value
    $localUserSddl = "D:(A;;CC;;;$sid)"
    $rules = @(
        @{Name='Multica Workflow Secure Runtime - Codex direct egress block'; Program=$CodexPath},
        @{Name='Multica Workflow Secure Runtime - Multica direct egress block'; Program=$MulticaPath}
    )
    foreach ($rule in $rules) {
        Remove-NetFirewallRule -DisplayName $rule.Name -ErrorAction SilentlyContinue
        New-NetFirewallRule -DisplayName $rule.Name -Direction Outbound -Action Block -Program $rule.Program -RemoteAddress Internet -LocalUser $localUserSddl | Out-Null
    }
}

Assert-Administrator

if ($ServiceName -ne 'MulticaWorkflowSecureRuntime') {
    throw 'ServiceName must match the reviewed WorkflowSecureDaemonHost service name.'
}

$artifactRoot = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$bundleRoot = (Resolve-Path -LiteralPath $WorkflowBundleRoot).Path
$multicaSource = Resolve-RequiredFile $MulticaExecutable 'Multica executable'
$codexSource = Resolve-RequiredFile $CodexExecutable 'Codex executable'
if ([IO.Path]::GetExtension($codexSource) -ne '.exe') {
    throw 'CodexExecutable must point to the real codex.exe binary, not a .cmd or .ps1 wrapper.'
}
$gitSource = Resolve-RequiredFile $GitExecutable 'Git executable'
$codexAuthSource = Resolve-RequiredFile $CodexAuthJson 'Dedicated Codex auth.json'
$appKeySource = Resolve-RequiredFile $MaintainerAppPrivateKey 'Maintainer App private key'
$bindingsSource = Resolve-RequiredFile $AgentBindings 'Agent bindings'
$manifestSource = Resolve-RequiredFile (Join-Path $bundleRoot 'secure-runtime\policy\workflow-bundle.manifest.json') 'Workflow bundle manifest'
$profileSource = (Resolve-Path -LiteralPath $MulticaProfileDirectory).Path
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$bindings = Get-Content -Raw -LiteralPath $bindingsSource | ConvertFrom-Json
$runtimeId = [Guid]::Empty
if ($bindings.schema_version -ne 1 -or
    -not [Guid]::TryParse([string]$bindings.runtime_id, [ref]$runtimeId) -or
    $runtimeId -eq [Guid]::Empty -or
    $null -eq $bindings.agents -or
    @($bindings.agents.PSObject.Properties).Count -eq 0) {
    throw 'Agent bindings must contain one non-empty secure Runtime UUID and managed Agent mappings.'
}

$requiredArtifacts = @(
    'secure-agent-launcher.exe',
    'workflow-token-broker.exe',
    'workflow-secure-daemon-host.exe',
    'workflow-console.exe'
)
foreach ($name in $requiredArtifacts) {
    Resolve-RequiredFile (Join-Path $artifactRoot $name) "Published artifact $name" | Out-Null
}

$paths = [ordered]@{
    bin = Join-Path $InstallRoot 'bin'
    config = Join-Path $InstallRoot 'config'
    credentials = Join-Path $InstallRoot 'credentials'
    codexCredentials = Join-Path $InstallRoot 'credentials\codex'
    githubCredentials = Join-Path $InstallRoot 'credentials\github'
    serviceHome = Join-Path $InstallRoot 'service-home'
    bundle = Join-Path $InstallRoot 'bundle'
    workspaces = Join-Path $InstallRoot 'workspaces'
    audit = Join-Path $InstallRoot 'audit'
}

if ($PSCmdlet.ShouldProcess($InstallRoot, 'Install secure Multica workflow runtime')) {
    $installPath = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $backupRoot = "$installPath.rollback-$([Guid]::NewGuid().ToString('N'))"
    $existingService = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    $hadService = $null -ne $existingService
    $createdService = $false
    $hadInstall = Test-Path -LiteralPath $installPath
    $backupCreated = $false
    $newInstallCreated = $false
    $oldRuntime = $null
    if ($hadService -ne $hadInstall) {
        throw 'Secure runtime service and installation root are inconsistent; repair or uninstall before reinstalling.'
    }
    if ($hadService -and $existingService.StartName -ne "NT SERVICE\$ServiceName") {
        throw 'Existing secure runtime service identity differs from the reviewed virtual service account.'
    }
    if ($hadService -and $hadInstall) {
        $oldRuntimePath = Join-Path $installPath 'runtime.local.json'
        if (-not (Test-Path -LiteralPath $oldRuntimePath -PathType Leaf)) {
            throw 'Existing secure runtime has no rollback configuration.'
        }
        $oldRuntime = Get-Content -Raw -LiteralPath $oldRuntimePath | ConvertFrom-Json
    }
    try {
        if ($hadService) {
            & sc.exe stop $ServiceName *> $null
            $service = Get-Service -Name $ServiceName -ErrorAction Stop
            $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        }
        if ($hadInstall) {
            Move-Item -LiteralPath $installPath -Destination $backupRoot
            $backupCreated = $true
        }

        New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
        $newInstallCreated = $true
        & icacls.exe $InstallRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to secure the staging installation root.' }
        New-Item -ItemType Directory -Force -Path $paths.Values | Out-Null
        if (Test-Path -LiteralPath $backupRoot) {
            foreach ($relative in @('workspaces', 'audit')) {
                $preservedSource = Join-Path $backupRoot $relative
                $preservedTarget = Join-Path $InstallRoot $relative
                if (Test-Path -LiteralPath $preservedSource) {
                    Remove-Item -LiteralPath $preservedTarget -Recurse -Force -ErrorAction SilentlyContinue
                    Copy-Item -LiteralPath $preservedSource -Destination $preservedTarget -Recurse -Force
                }
            }
        }
        foreach ($name in $requiredArtifacts) {
            Copy-Item -LiteralPath (Join-Path $artifactRoot $name) -Destination (Join-Path $paths.bin $name) -Force
        }
        Copy-Item -LiteralPath $multicaSource -Destination (Join-Path $paths.bin 'multica.exe') -Force
        Reset-DirectoryUnder $InstallRoot $paths.bundle
        Copy-DirectoryContents $bundleRoot $paths.bundle
        Copy-Item -LiteralPath $codexAuthSource -Destination (Join-Path $paths.codexCredentials 'auth.json') -Force
        Copy-Item -LiteralPath $appKeySource -Destination (Join-Path $paths.githubCredentials 'maintainer-app.pem') -Force
        Copy-Item -LiteralPath $bindingsSource -Destination (Join-Path $paths.config 'agent-bindings.local.json') -Force

        $profileTarget = Join-Path $paths.serviceHome ".multica\profiles\$MulticaProfile"
        Reset-DirectoryUnder $paths.serviceHome $profileTarget
        Copy-DirectoryContents $profileSource $profileTarget

        $key = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Fill($key)
        [Convert]::ToBase64String($key) | Set-Content -LiteralPath (Join-Path $paths.credentials 'capability.key') -Encoding ascii -NoNewline

        $runtime = [ordered]@{
            schema_version = 1
            repository = 'rberyou/multica-dev-workflow'
            service_root = $InstallRoot
            workspaces_root = $paths.workspaces
            workflow_bundle_root = $paths.bundle
            workflow_bundle_manifest = Join-Path $paths.bundle 'secure-runtime\policy\workflow-bundle.manifest.json'
            multica_executable = @{path=(Join-Path $paths.bin 'multica.exe'); sha256=Get-Sha256 (Join-Path $paths.bin 'multica.exe')}
            codex_executable = @{path=$codexSource; sha256=Get-Sha256 $codexSource}
            git_executable = @{path=$gitSource; sha256=Get-Sha256 $gitSource}
            launcher_executable = @{path=(Join-Path $paths.bin 'secure-agent-launcher.exe'); sha256=Get-Sha256 (Join-Path $paths.bin 'secure-agent-launcher.exe')}
            broker_executable = @{path=(Join-Path $paths.bin 'workflow-token-broker.exe'); sha256=Get-Sha256 (Join-Path $paths.bin 'workflow-token-broker.exe')}
            console_executable = @{path=(Join-Path $paths.bin 'workflow-console.exe'); sha256=Get-Sha256 (Join-Path $paths.bin 'workflow-console.exe')}
            codex_credential_path = Join-Path $paths.codexCredentials 'auth.json'
            agent_bindings_path = Join-Path $paths.config 'agent-bindings.local.json'
            broker_pipe_name = 'multica-workflow-broker'
            proxy_port = 46183
            multica_server_url = 'https://api.multica.ai'
            multica_profile = $MulticaProfile
            multica_daemon_id = $DaemonId
            multica_runtime_name = $RuntimeName
            capability_key_path = Join-Path $paths.credentials 'capability.key'
            audit_log_path = Join-Path $paths.audit 'runtime.jsonl'
            github = @{
                api_url = 'https://api.github.com'
                app_id = $MaintainerAppId
                installation_id = $MaintainerInstallationId
                private_key_path = Join-Path $paths.githubCredentials 'maintainer-app.pem'
            }
            profiles = @{
                workflow_maintainer = @{
                    permission_profile = 'workflow_maintainer'
                    github_mode = 'maintainer_broker'
                    allowed_branch_prefixes = @('stabilize/', 'fix/', 'docs/')
                    allowed_base_branches = @('main')
                    required_skills = @('multica-workflow-maintainer', 'multica-workflow-observer')
                }
                workflow_reviewer = @{
                    permission_profile = 'workflow_reviewer'
                    github_mode = 'none'
                    allowed_branch_prefixes = @()
                    allowed_base_branches = @()
                    required_skills = @('multica-workflow-maintainer', 'multica-workflow-observer')
                }
            }
            denied_roots = @((Join-Path $env:SystemDrive 'Users'))
            network_domains = @('api.multica.ai','api.github.com','github.com','objects.githubusercontent.com','raw.githubusercontent.com','api.openai.com','**.openai.com','chatgpt.com')
        }
        $runtimePath = Join-Path $InstallRoot 'runtime.local.json'
        $runtime | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $runtimePath -Encoding utf8

        $hostExe = Join-Path $paths.bin 'workflow-secure-daemon-host.exe'
        $binPath = '"{0}" --service --config "{1}"' -f $hostExe, $runtimePath
        $serviceAccount = "NT SERVICE\$ServiceName"
        if (-not $hadService) {
            & sc.exe create $ServiceName binPath= $binPath start= demand obj= $serviceAccount DisplayName= 'Multica Workflow Secure Runtime' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Failed to create Windows service $ServiceName." }
            $createdService = $true
            & sc.exe description $ServiceName 'Credential-isolated Multica daemon, Codex launcher and GitHub Token Broker.' | Out-Null
        }

        & icacls.exe $InstallRoot /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' "$serviceAccount`:(OI)(CI)F" /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to apply secure runtime ACLs.' }
        & icacls.exe $paths.bin /grant:r 'BUILTIN\Users:(OI)(CI)RX' /T /C | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Failed to apply executable read ACLs.' }
        Set-SecureFirewallRules $codexSource (Join-Path $paths.bin 'multica.exe') $serviceAccount
        & sc.exe start $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to start Windows service $ServiceName." }
        $service = Get-Service -Name $ServiceName -ErrorAction Stop
        $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        if (Test-Path -LiteralPath $backupRoot) {
            try {
                Remove-Item -LiteralPath $backupRoot -Recurse -Force
            } catch {
                Write-Warning "Secure runtime committed, but rollback cleanup must be completed manually: $backupRoot"
            }
        }
        Write-Output $runtimePath
    } catch {
        $failure = $_.Exception.Message
        $rollbackService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($null -ne $rollbackService -and $rollbackService.Status -ne 'Stopped') {
            & sc.exe stop $ServiceName *> $null
            $rollbackService.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        }
        if ($createdService) {
            & sc.exe delete $ServiceName *> $null
        }
        Remove-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Codex direct egress block' -ErrorAction SilentlyContinue
        Remove-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Multica direct egress block' -ErrorAction SilentlyContinue
        if ($backupCreated) {
            if (Test-Path -LiteralPath $installPath) {
                Remove-Item -LiteralPath $installPath -Recurse -Force
            }
            Move-Item -LiteralPath $backupRoot -Destination $installPath
        } elseif (-not $hadInstall -and $newInstallCreated -and (Test-Path -LiteralPath $installPath)) {
            Remove-Item -LiteralPath $installPath -Recurse -Force
        }
        if ($hadService -and $null -ne $oldRuntime) {
            Set-SecureFirewallRules ([string]$oldRuntime.codex_executable.path) ([string]$oldRuntime.multica_executable.path) "NT SERVICE\$ServiceName"
            & sc.exe start $ServiceName | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Secure runtime installation failed and rollback could not restart the previous service: $failure"
            }
            $rollbackService = Get-Service -Name $ServiceName -ErrorAction Stop
            $rollbackService.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        }
        throw "Secure runtime installation failed and the previous installation was restored: $failure"
    }
}
