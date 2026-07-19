[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$AgentBindings,
    [string]$InstallRoot = "$env:ProgramData\MulticaWorkflow",
    [string]$ServiceName = "MulticaWorkflowSecureRuntime"
)

$ErrorActionPreference = 'Stop'

$source = (Resolve-Path -LiteralPath $AgentBindings -ErrorAction Stop).Path
$bindings = Get-Content -Raw -LiteralPath $source | ConvertFrom-Json
$runtimeId = [Guid]::Empty
if ($bindings.schema_version -ne 1 -or
    $bindings.bootstrap -eq $true -or
    -not [Guid]::TryParse([string]$bindings.runtime_id, [ref]$runtimeId) -or
    $runtimeId -eq [Guid]::Empty -or
    $null -eq $bindings.agents -or
    @($bindings.agents.PSObject.Properties).Count -eq 0) {
    throw 'Final Agent bindings must contain the dedicated Runtime UUID and managed Agent mappings.'
}

$targetDirectory = [IO.Path]::GetFullPath((Join-Path $InstallRoot 'config'))
$target = [IO.Path]::GetFullPath((Join-Path $targetDirectory 'agent-bindings.local.json'))
$expectedPrefix = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\') + '\'
if (-not $target.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to update bindings outside the secure installation root.'
}
if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
    throw 'Existing secure Runtime Agent bindings are missing; use the reviewed installer.'
}
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction Stop
if ($service.StartName -ne "NT SERVICE\$ServiceName") {
    throw 'Secure runtime service identity differs from the reviewed virtual service account.'
}

if ($PSCmdlet.ShouldProcess($target, 'Install final secure Runtime Agent bindings and restart service')) {
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    $staged = "$target.new"
    $backup = "$target.rollback"
    $backupCreated = $false
    Copy-Item -LiteralPath $source -Destination $staged -Force
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    try {
        & sc.exe stop $ServiceName *> $null
        $windowsService = Get-Service -Name $ServiceName -ErrorAction Stop
        $windowsService.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            Move-Item -LiteralPath $target -Destination $backup
            $backupCreated = $true
        }
        Move-Item -LiteralPath $staged -Destination $target
        & sc.exe start $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to restart Windows service $ServiceName." }
        $windowsService = Get-Service -Name $ServiceName -ErrorAction Stop
        $windowsService.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        try {
            Remove-Item -LiteralPath $backup -Force -ErrorAction Stop
        } catch {
            Write-Warning "Bindings update committed, but rollback cleanup must be completed manually: $backup"
        }
        Write-Output $target
    } catch {
        $failure = $_.Exception.Message
        $rollbackService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($null -ne $rollbackService -and $rollbackService.Status -ne 'Stopped') {
            & sc.exe stop $ServiceName *> $null
            $rollbackService.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
        }
        if ($backupCreated) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
            Move-Item -LiteralPath $backup -Destination $target
        }
        & sc.exe start $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Agent bindings update failed and rollback could not restart the previous service: $failure"
        }
        $rollbackService = Get-Service -Name $ServiceName -ErrorAction Stop
        $rollbackService.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        throw "Agent bindings update failed and previous bindings were restored: $failure"
    } finally {
        Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    }
}
