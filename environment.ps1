# Process-local project paths; no global Windows settings are changed.
$ShortsProjectRoot = 'D:\유튜브 자동 쇼츠 생성'
if (-not (Test-Path -LiteralPath (Join-Path $ShortsProjectRoot 'AGENTS.md'))) {
    throw 'D drive project is unavailable. Restore/mount D before running this project.'
}
$ShortsCacheRoot = Join-Path $ShortsProjectRoot '.cache'
$ShortsTempRoot = Join-Path $ShortsProjectRoot 'tmp'
foreach ($taskDirectory in @($ShortsCacheRoot, $ShortsTempRoot)) {
    New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
}
$env:TEMP = $ShortsTempRoot
$env:TMP = $ShortsTempRoot
$env:PIP_CACHE_DIR = Join-Path $ShortsCacheRoot 'pip'
$env:HF_HOME = Join-Path $ShortsCacheRoot 'huggingface'
$env:TORCH_HOME = Join-Path $ShortsCacheRoot 'torch'
$env:XDG_CACHE_HOME = $ShortsCacheRoot
$env:UV_CACHE_DIR = Join-Path $ShortsCacheRoot 'uv'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
