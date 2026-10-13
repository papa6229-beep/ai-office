"""Bounded Illustrator export of a disposable copy; never save the source."""
import io
import os
import pathlib
import subprocess
import tempfile

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_EDGE = 1600
TIMEOUT_SECONDS = 60

# Paths enter PowerShell as arguments, then ExtendScript as numeric character
# codes. Neither shell syntax nor JavaScript syntax can come from a filename.
_SCRIPT = r'''
param([string]$SourcePath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
function JsPath([string]$Value) {
    $codes = $Value.ToCharArray() | ForEach-Object { [int]$_ }
    return 'String.fromCharCode(' + ($codes -join ',') + ')'
}
$src = JsPath $SourcePath
$dst = JsPath $OutputPath
$app = $null
try {
    $app = New-Object -ComObject Illustrator.Application
    $code = @'
(function(source, output) {
    var previous = app.userInteractionLevel;
    var doc = null;
    try {
        app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
        doc = app.open(new File(source));
        if (!doc.artboards.length) throw new Error('No artboard');
        doc.artboards.setActiveArtboardIndex(0);
        var r = doc.artboards[0].artboardRect;
        var edge = Math.max(Math.abs(r[2]-r[0]), Math.abs(r[1]-r[3]));
        if (!(edge > 0) || !isFinite(edge)) throw new Error('Invalid artboard');
        var options = new ExportOptionsJPEG();
        options.artBoardClipping = true;
        options.antiAliasing = true;
        options.qualitySetting = 80;
        options.horizontalScale = options.verticalScale = Math.min(100, 1598 / edge * 100);
        doc.exportFile(new File(output), ExportType.JPEG, options);
    } finally {
        try { if (doc) doc.close(SaveOptions.DONOTSAVECHANGES); }
        finally { app.userInteractionLevel = previous; }
    }
})(SOURCE_PATH, OUTPUT_PATH);
'@
    $code = $code.Replace('SOURCE_PATH', $src).Replace('OUTPUT_PATH', $dst)
    $null = $app.DoJavaScript($code)
} catch {
    # Do not echo Adobe exceptions: they can contain private document paths.
    [Console]::Error.WriteLine('Illustrator preview export failed.')
    exit 1
} finally {
    if ($null -ne $app) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }
}
'''


def render_ai_preview(path):
    """Return first-artboard JPEG bytes from Windows Illustrator automation.

    Illustrator is never quit. A timeout stops the helper process; a COM call
    already executing in Illustrator may finish later. Only the disposable
    document is opened and closed, and no document is saved.
    """
    source = pathlib.Path(path).resolve(strict=True)
    if source.suffix.lower() != '.ai' or not source.is_file():
        raise ValueError('Illustrator 미리보기에는 일반 AI 파일을 선택하세요.')
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('AI 파일이 미리보기 제한(64MB)을 넘습니다.')
    if os.name != 'nt':
        raise ValueError('AI 미리보기는 Illustrator가 설치된 Windows에서 지원합니다.')
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError('AI 미리보기에는 Pillow가 필요합니다. 앱 실행 Python에서 python -m pip install Pillow를 실행하세요.') from exc
    with tempfile.TemporaryDirectory(prefix='ai-office-preview-') as folder:
        temporary = pathlib.Path(folder)
        script = temporary / 'export.ps1'
        copied_source = temporary / 'source.ai'
        output = temporary / 'preview'
        rendered_file = pathlib.Path(str(output) + '.jpg')
        # Read through a capped stream, so a concurrently enlarged file cannot
        # bypass the size check. Copying protects an already-open original.
        with source.open('rb') as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError('AI 파일이 미리보기 제한(64MB)을 넘습니다.')
        copied_source.write_bytes(data)
        script.write_text(_SCRIPT, encoding='utf-8-sig')
        command = ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                   '-ExecutionPolicy', 'Bypass', '-File', str(script),
                   '-SourcePath', str(copied_source), '-OutputPath', str(output)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Illustrator 미리보기 시간이 60초를 넘었습니다. Illustrator 상태를 확인한 후 다시 시도하세요.') from exc
        except OSError as exc:
            raise ValueError('Windows PowerShell을 실행할 수 없습니다. Windows 설치 상태를 확인하세요.') from exc
        if result.returncode != 0 or not rendered_file.is_file():
            raise ValueError('Illustrator 미리보기 내보내기에 실패했습니다. Illustrator 설치·실행 상태와 파일을 확인하세요.')
        with rendered_file.open('rb') as stream:
            rendered = stream.read(MAX_OUTPUT_BYTES + 1)
        if len(rendered) > MAX_OUTPUT_BYTES:
            raise ValueError('AI 미리보기 크기가 제한(2MB)을 넘습니다.')
        try:
            with Image.open(io.BytesIO(rendered)) as image:
                if image.format != 'JPEG' or min(image.size) < 1 or max(image.size) > MAX_EDGE:
                    raise ValueError('AI 미리보기 형식 또는 해상도가 제한을 벗어났습니다.')
                image.load()
                clean = image.convert('RGB')
                output = io.BytesIO()
                clean.save(output, format='JPEG', quality=85)
                rendered = output.getvalue()
        except (OSError, SyntaxError) as exc:
            raise ValueError('Illustrator가 만든 JPEG 미리보기를 읽을 수 없습니다.') from exc
        if len(rendered) > MAX_OUTPUT_BYTES:
            raise ValueError('정리한 AI 미리보기 크기가 제한(2MB)을 넘습니다.')
        return rendered
