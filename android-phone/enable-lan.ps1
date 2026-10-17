#Requires -RunAsAdministrator
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$LocalAddress
)
$ErrorActionPreference='Stop'
$officeRoot=Split-Path $PSScriptRoot -Parent
$statusFolder=Join-Path $officeRoot '.provider_runtime\phone'
New-Item -ItemType Directory -Path $statusFolder -Force | Out-Null
$statusFile=Join-Path $statusFolder 'firewall-status.json'
try {
    $resolvedPython=(Resolve-Path -LiteralPath $PythonPath).Path
    if ([IO.Path]::GetFileName($resolvedPython) -ne 'python.exe') { throw 'Expected office Python executable' }
    $address=[Net.IPAddress]::Parse($LocalAddress)
    if ($address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $LocalAddress -notmatch '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)') { throw 'Expected private LAN IPv4 address' }
    Get-NetIPAddress -IPAddress $LocalAddress -AddressFamily IPv4 -ErrorAction Stop | Out-Null
    $name='AI-OFFICE-Phone-LAN-8766'
    $existing=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        $ports=$existing | Get-NetFirewallPortFilter
        $apps=$existing | Get-NetFirewallApplicationFilter
        $scope=$existing | Get-NetFirewallAddressFilter
        if ($ports.LocalPort -ne '8766' -or $apps.Program -ne $resolvedPython -or $scope.LocalAddress -notcontains $LocalAddress -or $scope.RemoteAddress -notcontains 'LocalSubnet') { throw 'Existing rule differs; not overwritten' }
    } else {
        New-NetFirewallRule -Name $name -DisplayName 'AI OFFICE phone notifications (local network only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Program $resolvedPython -LocalAddress $LocalAddress -RemoteAddress LocalSubnet -Profile Any | Out-Null
    }
    @{ok=$true;rule=$name;port=8766;scope='LocalSubnet';local_address=$LocalAddress} | ConvertTo-Json -Compress | Set-Content -LiteralPath $statusFile -Encoding utf8
} catch {
    @{ok=$false;message='Windows network permission was not completed.'} | ConvertTo-Json -Compress | Set-Content -LiteralPath $statusFile -Encoding utf8
    exit 1
}
