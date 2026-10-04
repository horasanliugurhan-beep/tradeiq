param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $runtime = $bundledPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $runtime = (Get-Command python).Source
} else { throw 'Python 3.10 veya daha yeni bir surum gerekli.' }
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Port 1024-65535 arasinda olmali.' }
Write-Output "Panel adresi: http://127.0.0.1:$Port"
Write-Output 'Uygulamayi durdurmak icin Ctrl+C. Acik sanal pozisyonlar dosyada saklanir.'
& $runtime (Join-Path $PSScriptRoot 'dashboard.py') --port $Port
if ($LASTEXITCODE -ne 0) { throw "Panel baslatilamadi: $LASTEXITCODE" }
