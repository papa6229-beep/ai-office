param(
    [string]$PythonPath = '',
    [switch]$DesktopShortcut
)
$ErrorActionPreference = 'Stop'
$desktopProject = Split-Path -Parent $PSScriptRoot
$desktopPrivate = Join-Path $desktopProject 'local_backups\desktop-app'
$desktopOutput = Join-Path $desktopPrivate 'bin'
$desktopSdkVersion = '1.0.4258.31'
$desktopPackage = Join-Path $desktopPrivate "Microsoft.Web.WebView2.$desktopSdkVersion.zip"
$desktopSdk = Join-Path $desktopPrivate "sdk-$desktopSdkVersion"
New-Item -ItemType Directory -Path $desktopOutput -Force | Out-Null

if (-not (Test-Path -LiteralPath $desktopPackage)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$desktopSdkVersion/microsoft.web.webview2.$desktopSdkVersion.nupkg" -OutFile "$desktopPackage.partial"
    Move-Item -LiteralPath "$desktopPackage.partial" -Destination $desktopPackage
}
if (-not (Test-Path -LiteralPath (Join-Path $desktopSdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'))) {
    Expand-Archive -LiteralPath $desktopPackage -DestinationPath $desktopSdk -Force
}
$desktopLoader = Join-Path $desktopSdk 'runtimes\win-x64\native\WebView2Loader.dll'
$desktopSignature = Get-AuthenticodeSignature -LiteralPath $desktopLoader
if ($desktopSignature.Status -ne 'Valid' -or $desktopSignature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'Microsoft WebView2 loader signature could not be verified.'
}

if (-not $PythonPath) {
    $desktopCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\pythonw.exe'),
        (Join-Path $desktopProject '.venv\Scripts\pythonw.exe')
    )
    $PythonPath = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath)) { throw 'Set -PythonPath to the existing Office Python executable.' }

# No package install or model call; use the Python environment already serving Office.
$desktopPythonCheck = Join-Path (Split-Path -Parent $PythonPath) 'python.exe'
& $desktopPythonCheck -c 'import PIL,playwright,cryptography,qrcode'
if ($LASTEXITCODE -ne 0) { throw 'Office Python dependencies are unavailable.' }

Add-Type -AssemblyName System.Drawing
$desktopBitmap = New-Object System.Drawing.Bitmap(64, 64)
$desktopGraphics = [System.Drawing.Graphics]::FromImage($desktopBitmap)
$desktopGraphics.SmoothingMode = 'AntiAlias'
$desktopGraphics.Clear([System.Drawing.Color]::FromArgb(17, 20, 21))
$desktopBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(191, 255, 25))
$desktopFont = New-Object System.Drawing.Font('Arial', 41, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$desktopGraphics.DrawString('A', $desktopFont, $desktopBrush, 12, 7)
$desktopGraphics.FillRectangle($desktopBrush, 8, 54, 48, 4)
$desktopIconHandle = $desktopBitmap.GetHicon()
$desktopIcon = [System.Drawing.Icon]::FromHandle($desktopIconHandle)
$desktopIconPath = Join-Path $desktopOutput 'aemeath.ico'
$desktopIconFile = [System.IO.File]::Create($desktopIconPath)
try { $desktopIcon.Save($desktopIconFile) } finally {
    $desktopIconFile.Dispose(); $desktopIcon.Dispose(); $desktopFont.Dispose()
    $desktopBrush.Dispose(); $desktopGraphics.Dispose(); $desktopBitmap.Dispose()
}

$desktopCore = Join-Path $desktopSdk 'lib\net462\Microsoft.Web.WebView2.Core.dll'
$desktopForms = Join-Path $desktopSdk 'lib\net462\Microsoft.Web.WebView2.WinForms.dll'
$desktopCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$desktopExe = Join-Path $desktopOutput 'Aemeath.exe'
& $desktopCompiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$desktopExe" "/win32icon:$desktopIconPath" "/win32manifest:$PSScriptRoot\app.manifest" "/reference:$desktopCore" "/reference:$desktopForms" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "$PSScriptRoot\AemeathDesktop.cs" "$PSScriptRoot\NativeMessengerHost.cs"
if ($LASTEXITCODE -ne 0) { throw 'Aemeath desktop compilation failed.' }
Copy-Item -LiteralPath $desktopCore -Destination $desktopOutput -Force
Copy-Item -LiteralPath $desktopForms -Destination $desktopOutput -Force
Copy-Item -LiteralPath $desktopLoader -Destination $desktopOutput -Force
[System.IO.File]::WriteAllText((Join-Path $desktopOutput 'python-path.txt'), (Resolve-Path -LiteralPath $PythonPath).Path, (New-Object System.Text.UTF8Encoding($false)))
$desktopMetadata = @{
    sdk_version=$desktopSdkVersion
    package_sha256=(Get-FileHash -LiteralPath $desktopPackage -Algorithm SHA256).Hash
    loader_signer=$desktopSignature.SignerCertificate.Subject
    executable=$desktopExe
    built_at=(Get-Date).ToUniversalTime().ToString('o')
}
$desktopMetadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $desktopPrivate 'build-receipt.json') -Encoding UTF8
if ($DesktopShortcut) {
    $desktopFolder = [Environment]::GetFolderPath('DesktopDirectory')
    $desktopShortcutPath = Join-Path $desktopFolder '에이메스.lnk'
    if (Test-Path -LiteralPath $desktopShortcutPath) { throw 'An existing 에이메스.lnk was preserved. Rename it before creating the new shortcut.' }
    $desktopShell = New-Object -ComObject WScript.Shell
    $desktopLink = $desktopShell.CreateShortcut($desktopShortcutPath)
    $desktopLink.TargetPath = $desktopExe; $desktopLink.WorkingDirectory = $desktopProject
    $desktopLink.IconLocation = "$desktopExe,0"
    $desktopLink.Description = '에이메스 · AI OFFICE 독립 프로그램'
    $desktopLink.Save()
}
Write-Output $desktopExe
