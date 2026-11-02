#Requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ServerPath = $(if ($env:RECAST_LLAMA_SERVER) { $env:RECAST_LLAMA_SERVER } else { '' }),
    [ValidateRange(1024, 65535)]
    [int]$Port = 8017,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$recastRoot = Split-Path -Parent $PSScriptRoot
$recastPreset = Join-Path $recastRoot 'config\models.ini'
$recastCache = Join-Path $recastRoot '.runtime\model-cache'

if (-not $ServerPath) {
    $recastFound = Get-Command llama-server -ErrorAction SilentlyContinue
    if ($recastFound) { $ServerPath = $recastFound.Source }
}
if (-not $ServerPath -or -not (Test-Path -LiteralPath $ServerPath -PathType Leaf)) {
    throw "llama-server executable not found. Set RECAST_LLAMA_SERVER, pass -ServerPath, or put llama-server on PATH."
}
if (-not (Test-Path -LiteralPath $recastPreset -PathType Leaf)) {
    throw "Router preset not found: $recastPreset"
}

$recastModelPaths = @(
    Get-Content -LiteralPath $recastPreset | ForEach-Object {
        if ($_ -match '^\s*model\s*=\s*(.+?)\s*$') { Join-Path $recastRoot $Matches[1] }
    }
)
if ($recastModelPaths.Count -lt 1 -or $recastModelPaths.Count -gt 5) {
    throw 'Expected between one and five explicit local model paths in config/models.ini.'
}
foreach ($recastModelPath in $recastModelPaths) {
    if (-not (Test-Path -LiteralPath $recastModelPath -PathType Leaf)) {
        throw "Model file not found: $recastModelPath"
    }
}

$recastArguments = @(
    '--host', '127.0.0.1',
    '--port', $Port.ToString(),
    '--models-preset', $recastPreset,
    '--models-max', '1',
    '--models-autoload',
    '--offline',
    '--no-webui',
    '--no-ui-mcp-proxy',
    '--timeout', '600'
)

if ($ValidateOnly) {
    [pscustomobject]@{
        Server = $ServerPath
        Preset = $recastPreset
        BaseUrl = "http://127.0.0.1:$Port/v1"
        Models = $recastModelPaths
        ConfiguredModelCount = $recastModelPaths.Count
        MaximumLoadedModels = 1
        ContextPerModel = 16384
        IsolatedCache = $recastCache
        Arguments = $recastArguments
    } | ConvertTo-Json -Depth 3
    return
}

New-Item -ItemType Directory -Path $recastCache -Force | Out-Null

# Configure only the child process. Do not alter the user's shell environment.
$recastStart = [System.Diagnostics.ProcessStartInfo]::new()
$recastStart.FileName = $ServerPath
$recastStart.WorkingDirectory = $recastRoot
$recastStart.UseShellExecute = $false
$recastStart.CreateNoWindow = $true
foreach ($recastArgument in $recastArguments) {
    $recastStart.ArgumentList.Add($recastArgument)
}
foreach ($recastEnvKey in @($recastStart.Environment.Keys)) {
    if ($recastEnvKey.StartsWith('LLAMA_ARG_') -or $recastEnvKey -eq 'LLAMA_API_KEY') {
        $recastStart.Environment.Remove($recastEnvKey) | Out-Null
    }
}
$recastStart.Environment['LLAMA_CACHE'] = $recastCache

Write-Host "Recast router: http://127.0.0.1:$Port (one loaded model maximum)"
$recastProcess = [System.Diagnostics.Process]::Start($recastStart)
try {
    $recastProcess.WaitForExit()
    $recastExitCode = $recastProcess.ExitCode
}
finally {
    if (-not $recastProcess.HasExited) {
        $recastProcess.Kill($true)
        $recastProcess.WaitForExit()
    }
    $recastProcess.Dispose()
}
exit $recastExitCode
