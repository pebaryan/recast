#Requires -Version 7.0
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$recastRoot = Split-Path -Parent $PSScriptRoot
$recastSessionFile = Join-Path $recastRoot '.runtime\app-session.json'
if (-not (Test-Path -LiteralPath $recastSessionFile)) {
    Write-Host 'No managed Recast app session was found.'
    return
}
$recastSession = Get-Content -LiteralPath $recastSessionFile -Raw | ConvertFrom-Json
$recastProcess = Get-Process -Id $recastSession.pid -ErrorAction SilentlyContinue
if ($null -ne $recastProcess) {
    if ($recastProcess.StartTime.ToUniversalTime().Ticks -ne [long]$recastSession.started_utc_ticks -or $recastProcess.Path -ne $recastSession.executable) {
        throw 'The recorded PID belongs to a different process. No process was stopped.'
    }
    $recastProcess.Kill($true)
    $recastProcess.WaitForExit()
}
Remove-Item -LiteralPath $recastSessionFile
Write-Host 'Recast and any router it started have stopped. Completed job sections remain saved.'
