#Requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$Port = 8020,
    [ValidateRange(1024, 65535)][int]$RouterPort = 8017,
    [string]$Python = 'python',
    [string]$ServerPath = $(if ($env:RECAST_LLAMA_SERVER) { $env:RECAST_LLAMA_SERVER } else { '' })
)
$ErrorActionPreference = 'Stop'
$recastAppRoot = Split-Path -Parent $PSScriptRoot
$recastRouterUrl = "http://127.0.0.1:$RouterPort"
$recastOwnedRouter = $null
$recastAppExit = 1
$recastSessionFile = Join-Path $recastAppRoot '.runtime\app-session.json'
$recastSessionWritten = $false
Push-Location -LiteralPath $recastAppRoot
try {
    Get-Command $Python -ErrorAction Stop | Out-Null
    if (Test-Path -LiteralPath $recastSessionFile) {
        $recastPrevious = Get-Content -LiteralPath $recastSessionFile -Raw | ConvertFrom-Json
        $recastPreviousProcess = Get-Process -Id $recastPrevious.pid -ErrorAction SilentlyContinue
        if ($null -ne $recastPreviousProcess -and $recastPreviousProcess.StartTime.ToUniversalTime().Ticks -eq [long]$recastPrevious.started_utc_ticks -and $recastPreviousProcess.Path -eq $recastPrevious.executable) {
            throw "Recast is already managed at http://127.0.0.1:$($recastPrevious.port). Use scripts/stop-app.ps1 before starting another instance."
        }
    }
    $recastRouterReady = $false
    try { $recastRouterReady = (Invoke-RestMethod "$recastRouterUrl/health" -TimeoutSec 1).status -eq 'ok' } catch {}
    if (-not $recastRouterReady) {
        $recastStart = [System.Diagnostics.ProcessStartInfo]::new()
        $recastStart.FileName = (Get-Command pwsh).Source
        $recastStart.WorkingDirectory = $recastAppRoot
        $recastStart.UseShellExecute = $false
        $recastStart.CreateNoWindow = $true
        $recastRouterArgs = @('-NoProfile', '-File', (Join-Path $PSScriptRoot 'start-router.ps1'), '-Port', $RouterPort.ToString())
        if ($ServerPath) { $recastRouterArgs += @('-ServerPath', $ServerPath) }
        foreach ($recastArgument in $recastRouterArgs) {
            $recastStart.ArgumentList.Add($recastArgument)
        }
        $recastOwnedRouter = [System.Diagnostics.Process]::Start($recastStart)
        $recastDeadline = [System.Diagnostics.Stopwatch]::StartNew()
        while ($recastDeadline.Elapsed.TotalSeconds -lt 25 -and -not $recastRouterReady) {
            if ($recastOwnedRouter.HasExited) { throw 'The local router stopped during startup. Check the server path and model preset.' }
            try { $recastRouterReady = (Invoke-RestMethod "$recastRouterUrl/health" -TimeoutSec 1).status -eq 'ok' } catch {}
            if (-not $recastRouterReady) { Start-Sleep -Milliseconds 250 }
        }
        if (-not $recastRouterReady) { throw 'The local router did not become ready within 25 seconds.' }
    }
    $recastExpected = @(Get-Content -LiteralPath (Join-Path $recastAppRoot 'config\models.ini') | ForEach-Object { if ($_ -match '^\[(recast-[^\]]+)\]$') { $Matches[1] } } | Sort-Object)
    $recastActual = @((Invoke-RestMethod "$recastRouterUrl/models" -TimeoutSec 5).data.id | Sort-Object)
    if (Compare-Object $recastExpected $recastActual) { throw 'The service on the router port does not match Recast''s configured model allowlist.' }
    New-Item -ItemType Directory -Path (Split-Path -Parent $recastSessionFile) -Force | Out-Null
    $recastSelf = Get-Process -Id $PID
    @{ pid = $PID; started_utc_ticks = $recastSelf.StartTime.ToUniversalTime().Ticks; executable = $recastSelf.Path; port = $Port } | ConvertTo-Json | Set-Content -LiteralPath $recastSessionFile
    $recastSessionWritten = $true
    Write-Host "Open http://127.0.0.1:$Port to use Recast. Press Ctrl+C to stop."
    & $Python -X utf8 -m recast serve --port $Port --base-url $recastRouterUrl
    $recastAppExit = $LASTEXITCODE
}
finally {
    if ($null -ne $recastOwnedRouter) {
        if (-not $recastOwnedRouter.HasExited) {
            $recastOwnedRouter.Kill($true)
            $recastOwnedRouter.WaitForExit()
        }
        $recastOwnedRouter.Dispose()
    }
    if ($recastSessionWritten -and (Test-Path -LiteralPath $recastSessionFile)) {
        $recastCurrent = Get-Content -LiteralPath $recastSessionFile -Raw | ConvertFrom-Json
        if ([int]$recastCurrent.pid -eq $PID) { Remove-Item -LiteralPath $recastSessionFile }
    }
    Pop-Location
}
exit $recastAppExit
