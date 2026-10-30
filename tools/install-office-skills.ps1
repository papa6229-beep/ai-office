$ErrorActionPreference='Stop'
$skillProject=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$skillSource=Join-Path $skillProject 'skills\office-skills'
$skillDestination=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.agents\skills\office-skills'
$skillFiles=@('SKILL.md','agents\openai.yaml')
$skillReceiptPath=Join-Path $skillDestination '.office-install.json'
$skillPrevious=$null
if(Test-Path -LiteralPath $skillReceiptPath){$skillPrevious=Get-Content -LiteralPath $skillReceiptPath -Raw|ConvertFrom-Json}
if(Test-Path -LiteralPath $skillDestination){
    foreach($skillRelative in $skillFiles){
        $skillTarget=Join-Path $skillDestination $skillRelative
        if(Test-Path -LiteralPath $skillTarget){
            $skillCurrentHash=(Get-FileHash -LiteralPath $skillTarget).Hash
            if($skillCurrentHash -ne (Get-FileHash -LiteralPath (Join-Path $skillSource $skillRelative)).Hash -and (-not $skillPrevious -or $skillPrevious.files.$skillRelative -ne $skillCurrentHash)){throw 'Existing user gateway differs; preserved it'}
        }
    }
}
New-Item -ItemType Directory -Path (Join-Path $skillDestination 'agents') -Force | Out-Null
foreach($skillRelative in $skillFiles){Copy-Item -LiteralPath (Join-Path $skillSource $skillRelative) -Destination (Join-Path $skillDestination $skillRelative) -Force}
$skillHashes=@{}
foreach($skillRelative in $skillFiles){
    $skillHash=(Get-FileHash -LiteralPath (Join-Path $skillDestination $skillRelative)).Hash
    if($skillHash -ne (Get-FileHash -LiteralPath (Join-Path $skillSource $skillRelative)).Hash){throw 'Gateway copy mismatch'}
    $skillHashes[$skillRelative]=$skillHash
}
$skillReceipt=[pscustomobject]@{installed=$true;path=$skillDestination;files=$skillHashes;model_or_permission_settings_changed=$false;apps_restarted=$false}
$skillReceipt|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $skillReceiptPath -Encoding utf8
$skillReceipt|ConvertTo-Json -Depth 4 -Compress
