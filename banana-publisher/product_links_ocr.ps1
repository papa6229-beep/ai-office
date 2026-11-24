param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$ResultPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
$awaitMethod=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-Result($operation,[Type]$resultType) {
    $task=$awaitMethod.MakeGenericMethod($resultType).Invoke($null,@($operation))
    $task.GetAwaiter().GetResult()
}
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('ko'))
if (!$engine) { throw 'Korean OCR is unavailable' }
$images=[IO.File]::ReadAllText($InputPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
$output=@(foreach ($image in $images) {
    $file=Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image.path)) ([Windows.Storage.StorageFile])
    $stream=Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder=Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await-Result ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $result=Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            @{id=$image.id;lines=@(foreach ($line in $result.Lines) {
                @{text=$line.Text;words=@(foreach ($word in $line.Words) {
                    $rect=$word.BoundingRect
                    @{text=$word.Text;x=$rect.X;y=$rect.Y;width=$rect.Width;height=$rect.Height}
                })}
            })}
        } finally { $bitmap.Dispose() }
    } finally { $stream.Dispose() }
})
[IO.File]::WriteAllText($ResultPath,(ConvertTo-Json -InputObject $output -Depth 8),[Text.UTF8Encoding]::new($false))
