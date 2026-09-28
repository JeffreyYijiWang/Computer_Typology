[CmdletBinding()]
param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$InstallRoot = Join-Path $env:USERPROFILE '.computer-typology'
$AppRoot = Join-Path $InstallRoot 'app'
$RuntimeRoot = Join-Path $InstallRoot 'runtime'
$RuntimePython = Join-Path $RuntimeRoot 'Scripts\python.exe'
$RuntimePythonw = Join-Path $RuntimeRoot 'Scripts\pythonw.exe'

New-Item -ItemType Directory -Path $AppRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $RuntimePython)) {
    & $Python -m venv $RuntimeRoot
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python runtime.' }
}
& $RuntimePython -m pip install --disable-pip-version-check -r (Join-Path $SourceRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; recorder was not stopped.' }

# Preserve the running interval before replacing code. History, settings,
# browser pairing and the encrypted cloud connection stay in InstallRoot.
try { $null = Invoke-RestMethod 'http://127.0.0.1:43128/api/status' -TimeoutSec 3 } catch {}
$Launchers = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object {
    $_.ExecutablePath -eq $RuntimePythonw -and $_.CommandLine -match '\s-m\s+tracker(\s|$)'
})
foreach ($Launcher in $Launchers) {
    $Children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($Launcher.ProcessId)" | Where-Object {
        $_.CommandLine -match '\s-m\s+tracker(\s|$)'
    })
    foreach ($Child in $Children) {
        Stop-Process -Id $Child.ProcessId -ErrorAction SilentlyContinue
        Wait-Process -Id $Child.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
    }
    Stop-Process -Id $Launcher.ProcessId -ErrorAction SilentlyContinue
    Wait-Process -Id $Launcher.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
}

foreach ($Folder in @('tracker','web','extension','scripts','infra')) {
    $TargetFolder = Join-Path $AppRoot $Folder
    New-Item -ItemType Directory -Path $TargetFolder -Force | Out-Null
    Copy-Item -Path (Join-Path $SourceRoot "$Folder\*") -Destination $TargetFolder -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $SourceRoot 'requirements.txt') -Destination $AppRoot -Force

$ShortcutShell = New-Object -ComObject WScript.Shell
$ShortcutSource = Join-Path $InstallRoot 'Computer Typology.lnk'
$Shortcut = $ShortcutShell.CreateShortcut($ShortcutSource)
$Shortcut.TargetPath = $RuntimePythonw
$Shortcut.Arguments = '-m tracker'
$Shortcut.WorkingDirectory = $AppRoot
$Shortcut.Description = 'Computer Typology activity recorder and local dashboard'
$Shortcut.WindowStyle = 7
$Shortcut.Save()
foreach ($Folder in @([Environment]::GetFolderPath('Startup'),[Environment]::GetFolderPath('Programs'))) {
    $ShortcutTarget = Join-Path $Folder 'Computer Typology.lnk'
    Copy-Item -LiteralPath $ShortcutSource -Destination $ShortcutTarget -Force
    $Verified = $ShortcutShell.CreateShortcut($ShortcutTarget)
    if ($Verified.TargetPath -ne $RuntimePythonw -or $Verified.Arguments -ne '-m tracker' -or $Verified.WorkingDirectory -ne $AppRoot) {
        throw "Could not verify shortcut: $ShortcutTarget"
    }
}

Start-Process -FilePath $RuntimePythonw -ArgumentList '-m','tracker' -WorkingDirectory $AppRoot -WindowStyle Hidden -RedirectStandardError (Join-Path $InstallRoot 'startup-error.log')
$Ready = $false
for ($Attempt = 0; $Attempt -lt 12; $Attempt++) {
    try {
        $Status = Invoke-RestMethod 'http://127.0.0.1:43128/api/status' -TimeoutSec 2
        $Ready = $true
        break
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $Ready) { throw "The recorder did not respond. Check $InstallRoot\startup-error.log and security history." }
Write-Output "Installed for $env:USERNAME. Startup and Start menu shortcuts verified. Recorder: $($Status.state)."
Write-Output 'Dashboard: http://127.0.0.1:43128'
Write-Output 'Reload Computer Typology in Chrome and Edge extensions settings after an extension update.'
