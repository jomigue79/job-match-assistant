# Creates Desktop and Start Menu shortcuts to scripts\launch.bat.
# Run once:  powershell -ExecutionPolicy Bypass -File scripts\install_shortcut.ps1
# Windows PowerShell 5.1 compatible: no ternary, no ??, no && chaining.

$ErrorActionPreference = 'Stop'

# Resolve the repo root from this script's own location, not the caller's CWD.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir '..')).Path
$Target    = Join-Path $RepoRoot 'scripts\launch.bat'

if (-not (Test-Path -LiteralPath $Target)) {
    throw "Cannot find launch target: $Target"
}

# Use the project icon when present; otherwise fall back to a stock shell icon.
# No image dependency is added by this script.
$IconCandidate = Join-Path $RepoRoot 'assets\jma.ico'
if (Test-Path -LiteralPath $IconCandidate) {
    $IconLocation = "$IconCandidate,0"
} else {
    $IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
}

$ShortcutName = 'Job Match Assistant.lnk'
$Desktop      = [Environment]::GetFolderPath('Desktop')
$StartMenu    = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'

$Destinations = @(
    (Join-Path $Desktop   $ShortcutName),
    (Join-Path $StartMenu $ShortcutName)
)

$Shell = New-Object -ComObject WScript.Shell
foreach ($Path in $Destinations) {
    $Parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $Parent)) {
        Write-Warning "Skipping (folder missing): $Parent"
        continue
    }
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath       = $Target
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.IconLocation     = $IconLocation
    $Shortcut.Description      = 'Job Match Assistant - Operational Control Room'
    $Shortcut.Save()
    Write-Host "Created: $Path"
}

Write-Host ''
Write-Host "Target:  $Target"
Write-Host "Icon:    $IconLocation"
