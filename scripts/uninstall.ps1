[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$RuntimePython = Join-Path $env:USERPROFILE '.computer-typology\runtime\Scripts\pythonw.exe'
foreach ($Directory in @([Environment]::GetFolderPath('Startup'),[Environment]::GetFolderPath('Programs'))) {
    $ShortcutPath = Join-Path $Directory 'Computer Typology.lnk'
    if (Test-Path -LiteralPath $ShortcutPath) { Remove-Item -LiteralPath $ShortcutPath }
}
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object {
    $_.ExecutablePath -eq $RuntimePython -and $_.CommandLine -match '\s-m\s+tracker(\s|$)'
} | ForEach-Object {
    $LauncherId = $_.ProcessId
    Get-CimInstance Win32_Process -Filter "ParentProcessId = $LauncherId" | Where-Object { $_.CommandLine -match '\s-m\s+tracker(\s|$)' } | ForEach-Object { Stop-Process -Id $_.ProcessId }
    Stop-Process -Id $LauncherId -ErrorAction SilentlyContinue
}
Write-Output 'Stopped the tracker and removed automatic startup. Your local history, installed files, and AWS database have been preserved.'
Write-Output 'Remove the browser extension separately in Chrome / Edge. AWS resources continue to incur charges until deleted.'

