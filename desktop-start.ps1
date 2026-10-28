param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$url = 'http://127.0.0.1:8501/'
$runtimeFolder = Join-Path $root 'user_data'
$mutex = $null
$ownsMutex = $false

function Test-WorkbenchReady {
    $response = $null
    try {
        $request = [System.Net.HttpWebRequest]::Create($url + '_stcore/health')
        $request.Proxy = $null
        $request.Timeout = 1500
        $response = $request.GetResponse()
        return ([int]$response.StatusCode -eq 200)
    } catch {
        return $false
    } finally {
        if ($null -ne $response) { $response.Close() }
    }
}

function Open-Workbench {
    if (-not $NoBrowser) { Start-Process -FilePath $url }
}

try {
    # Keep the shortcut fixed; resolve the app's current Python on each click.
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $hash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant()))
    } finally { $hash.Dispose() }
    $mutexName = 'Local\PerpWorkbench-' + [BitConverter]::ToString($digest).Replace('-', '')
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) {
        if (Test-WorkbenchReady) { Open-Workbench }
        return
    }
    if (Test-WorkbenchReady) {
        Open-Workbench
        return
    }

    $pythonChoices = @(
        (Join-Path $root '.venv/Scripts/python.exe'),
        (Join-Path $root '../../work/quant-env/Scripts/python.exe')
    )
    $python = $pythonChoices | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $python) { throw 'Python environment missing. Run setup.ps1 in the project folder first.' }
    New-Item -ItemType Directory -Path $runtimeFolder -Force | Out-Null
    $arguments = '"' + (Join-Path $root 'launch.py') + '" start --no-browser'
    $service = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeFolder 'desktop-launcher.log') -RedirectStandardError (Join-Path $runtimeFolder 'desktop-launcher.error.log')
    $deadline = [DateTime]::UtcNow.AddSeconds(75)
    while ([DateTime]::UtcNow -lt $deadline) {
        $service.Refresh()
        if ($service.HasExited) { throw 'The monitor exited during startup. See the startup logs in user_data.' }
        if (Test-WorkbenchReady) {
            Open-Workbench
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw 'Startup has not finished. Check the logs in user_data, then try the shortcut again.'
} catch {
    if ($NoBrowser) { throw }
    Add-Type -AssemblyName System.Windows.Forms
    $message = "Startup failed: " + $_.Exception.Message + "`r`n`r`nLogs: " + $runtimeFolder
    [System.Windows.Forms.MessageBox]::Show($message, 'Binance Monitor', 'OK', 'Error') | Out-Null
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
