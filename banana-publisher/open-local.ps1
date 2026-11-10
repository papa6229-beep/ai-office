param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$publisherUrl = 'http://127.0.0.1:8877/'
$project = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $project 'local_backups\banana-publisher\start-local.ps1'
$browser = Join-Path $env:ProgramFiles 'Citro Labs\ego lite\Application\ego.exe'

function Test-Publisher {
    try {
        $page = Invoke-WebRequest -Uri $publisherUrl -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
        return $page.StatusCode -eq 200 -and $page.Content.Contains('id="prepare"') -and
            $page.Content.Contains('/static/publisher.js')
    } catch {
        return $false
    }
}

$mutex = [Threading.Mutex]::new($false, 'Local\AI-OFFICE.BananaPublisher.Start')
$locked = $false
try {
    try { $locked = $mutex.WaitOne([TimeSpan]::FromSeconds(25)) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (!$locked) { throw 'Timed out waiting for the publisher launcher.' }

    $ready = Test-Publisher
    if (!$ready) {
        if (!(Test-Path -LiteralPath $startScript -PathType Leaf)) { throw 'Publisher setup is missing on this PC.' }
        $listener = Get-NetTCPConnection -LocalPort 8877 -State Listen -ErrorAction SilentlyContinue
        if (!$listener) {
            $launcher = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $quotedScript = '"{0}"' -f $startScript
            Start-Process -FilePath $launcher -ArgumentList @('-NoLogo','-NoProfile','-File',$quotedScript) -WindowStyle Hidden
        }
        $deadline = [DateTime]::UtcNow.AddSeconds(18)
        do {
            Start-Sleep -Milliseconds 500
            $ready = Test-Publisher
        } until ($ready -or [DateTime]::UtcNow -ge $deadline)
        if (!$ready) { throw 'The local publisher did not start at 127.0.0.1:8877.' }
    }

    if (!(Test-Path -LiteralPath $browser -PathType Leaf)) { throw 'Ego is missing on this PC.' }
    if ($CheckOnly) {
        Write-Output $publisherUrl
    } else {
        Start-Process -FilePath $browser -ArgumentList @('--new-window',$publisherUrl) -WindowStyle Normal
    }
} catch {
    if ($CheckOnly) { throw }
    $shell = New-Object -ComObject WScript.Shell
    $null = $shell.Popup($_.Exception.Message, 10, 'AI OFFICE - BananaMall Event Publisher', 48)
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
