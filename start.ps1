$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '../../work/quant-env/Scripts/python.exe'
$localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (Test-Path -LiteralPath $localPython) {
    $selectedPython = $localPython
} elseif (Test-Path -LiteralPath $taskPython) {
    $selectedPython = $taskPython
} else {
    throw 'Please run setup.ps1 first to install Python dependencies.'
}
Push-Location $PSScriptRoot
try {
    & $selectedPython launch.py start
} finally {
    Pop-Location
}
