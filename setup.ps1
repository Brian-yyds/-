param([string]$PythonPath = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $PythonPath -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
    if ($LASTEXITCODE -ne 0) { throw 'A working Python 3.11+ installation is required.' }
    & $PythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment.' }
    $venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    & $venvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your connection to PyPI.' }
    Write-Host 'Ready. Run start.ps1 to open the monitoring dashboard.'
} finally {
    Pop-Location
}
