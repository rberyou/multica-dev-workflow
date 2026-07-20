[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$InstallRoot = "$env:ProgramData\MulticaWorkflow",
    [string]$ServiceName = "MulticaWorkflowSecureRuntime",
    [switch]$PurgeCredentials
)

$ErrorActionPreference = 'Stop'
if ($PSCmdlet.ShouldProcess($ServiceName, 'Stop and remove secure runtime service')) {
    & sc.exe stop $ServiceName *> $null
    Start-Sleep -Seconds 2
    & sc.exe delete $ServiceName *> $null
    Remove-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Codex direct egress block' -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName 'Multica Workflow Secure Runtime - Multica direct egress block' -ErrorAction SilentlyContinue
}
if ($PurgeCredentials -and $PSCmdlet.ShouldProcess($InstallRoot, 'Purge secure runtime credentials and task homes')) {
    foreach ($relative in @('credentials', 'tasks')) {
        $target = Join-Path $InstallRoot $relative
        if (Test-Path -LiteralPath $target) {
            Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        }
        if (Test-Path -LiteralPath $target) {
            throw "Secure runtime purge did not remove $target"
        }
    }
}
Write-Output 'Secure runtime disabled. Managed Agents and Observer must remain paused until a reviewed reinstall succeeds.'
