$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '../../work/quant-env/Scripts/python.exe'
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
$selectedPython = if (Test-Path -LiteralPath $localPython) { $localPython } else { $taskPython }
Push-Location $PSScriptRoot
try {
    & $selectedPython launch.py stop
} finally {
    Pop-Location
}
