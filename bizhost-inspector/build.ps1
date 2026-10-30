param([switch]$DesktopShortcut)
$ErrorActionPreference = 'Stop'
$inspectorRoot = Split-Path -Parent $PSScriptRoot
$inspectorOutput = Join-Path $inspectorRoot 'local_backups\bizhost-inspector\bin'
$inspectorExisting = Join-Path $inspectorRoot 'local_backups\desktop-app\bin'
$inspectorSdk = Join-Path $inspectorRoot 'local_backups\desktop-app\sdk-1.0.4258.31'
$inspectorPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\pythonw.exe'
foreach ($inspectorRequired in @($inspectorPython, (Join-Path $inspectorExisting 'WebView2Loader.dll'), (Join-Path $inspectorSdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'))) {
    if (-not (Test-Path -LiteralPath $inspectorRequired)) { throw 'Existing Office runtime / WebView2 SDK is required.' }
}
New-Item -ItemType Directory -Path $inspectorOutput -Force | Out-Null
$inspectorCore = Join-Path $inspectorSdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$inspectorForms = Join-Path $inspectorSdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$inspectorCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$inspectorExe = Join-Path $inspectorOutput 'BizhostInspector.exe'
Copy-Item -LiteralPath (Join-Path $inspectorExisting 'aemeath.ico') -Destination (Join-Path $inspectorOutput 'inspector.ico') -Force
& $inspectorCompiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$inspectorExe" "/win32icon:$inspectorOutput\inspector.ico" "/reference:$inspectorCore" "/reference:$inspectorForms" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "$PSScriptRoot\InspectorWindow.cs"
if ($LASTEXITCODE -ne 0) { throw 'Inspector compilation failed.' }
foreach ($inspectorDll in @($inspectorCore, $inspectorForms, (Join-Path $inspectorExisting 'WebView2Loader.dll'))) { Copy-Item -LiteralPath $inspectorDll -Destination $inspectorOutput -Force }
[System.IO.File]::WriteAllText((Join-Path $inspectorOutput 'python-path.txt'), $inspectorPython, (New-Object System.Text.UTF8Encoding($false)))
if ($DesktopShortcut) {
    $inspectorDesktop = [Environment]::GetFolderPath('DesktopDirectory')
    $inspectorLinkPath = Join-Path $inspectorDesktop '비즈호스트 상품 검수.lnk'
    if (Test-Path -LiteralPath $inspectorLinkPath) { throw 'Existing shortcut was preserved.' }
    $inspectorShell = New-Object -ComObject WScript.Shell
    $inspectorLink = $inspectorShell.CreateShortcut($inspectorLinkPath)
    $inspectorLink.TargetPath = $inspectorExe; $inspectorLink.WorkingDirectory = $inspectorRoot
    $inspectorLink.IconLocation = "$inspectorExe,0"; $inspectorLink.Description = '에이메스 · A/B/C군 상품 검수와 실제 완료 반영'
    $inspectorLink.Save()
}
Write-Output $inspectorExe
