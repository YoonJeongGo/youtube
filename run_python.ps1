# Forward arguments to the migrated project's Python using D-only project caches.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
Push-Location -LiteralPath $ShortsProjectRoot
try {
    & (Join-Path $ShortsProjectRoot '.venv\Scripts\python.exe') @args
    $runExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $runExit
