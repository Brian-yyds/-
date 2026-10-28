param([string]$DesktopPath = [Environment]::GetFolderPath('Desktop'))
$ErrorActionPreference = 'Stop'
$entry = Join-Path $PSScriptRoot 'desktop-start.vbs'
if (-not (Test-Path -LiteralPath $entry)) { throw 'Desktop launcher entry is missing.' }
if (-not (Test-Path -LiteralPath $DesktopPath)) { throw 'Desktop folder does not exist.' }
# Build the Chinese name from code points so Windows PowerShell 5.1 can read this UTF-8 script without a BOM.
$name = -join (0x5E01, 0x5B89, 0x5F02, 0x52A8, 0x76D1, 0x63A7 | ForEach-Object { [char]$_ })
$shortcutPath = Join-Path $DesktopPath ($name + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$hostPath = Join-Path $env:SystemRoot 'System32/wscript.exe'
if (Test-Path -LiteralPath $shortcutPath) {
    $previousEntry = $shortcut.Arguments.Trim('"')
    if ($shortcut.TargetPath -ine $hostPath -or [System.IO.Path]::GetFileName($previousEntry) -ne 'desktop-start.vbs') {
        throw 'An unrelated shortcut with the same name already exists.'
    }
}
$shortcut.TargetPath = $hostPath
$shortcut.Arguments = '"' + $entry + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32/perfmon.exe') + ',0'
$shortcut.Description = 'Start Binance market monitor or open the existing dashboard'
$shortcut.WindowStyle = 1
$shortcut.Save()
Write-Output $shortcutPath
