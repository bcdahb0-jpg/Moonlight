$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot 'backend'
$frontendDir = Join-Path $repoRoot 'frontend'
$failures = [System.Collections.Generic.List[string]]::new()

function Check-RequiredFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    $script:failures.Add("Missing file: $Path")
  }
}

Write-Host 'Moonlight release check'
Check-RequiredFile (Join-Path $repoRoot 'README.md')
Check-RequiredFile (Join-Path $backendDir 'pyproject.toml')
Check-RequiredFile (Join-Path $backendDir 'uv.lock')
Check-RequiredFile (Join-Path $frontendDir 'package.json')
Check-RequiredFile (Join-Path $frontendDir 'pnpm-lock.yaml')
Check-RequiredFile (Join-Path $frontendDir 'electron-builder.yml')

$trackedSecret = git -C $repoRoot ls-files -- 'backend/conf.yaml' 'frontend/.env'
if ($trackedSecret) {
  $failures.Add('Local secret config is tracked by Git: backend/conf.yaml or frontend/.env')
}

$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'
Check-RequiredFile $venvPython
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
  try {
    $version = & $venvPython --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "exit code $LASTEXITCODE" }
    Write-Host "Python runtime: $version"
  } catch {
    $failures.Add("backend/.venv cannot start; recreate it with Python 3.12 ($_) ")
  }
}

Push-Location $frontendDir
try {
  Write-Host 'Running frontend typecheck...'
  & npm.cmd run typecheck
  if ($LASTEXITCODE -ne 0) { $failures.Add('Frontend typecheck failed') }

  Write-Host 'Running frontend production build...'
  & npm.cmd run build
  if ($LASTEXITCODE -ne 0) { $failures.Add('Frontend production build failed') }
} finally {
  Pop-Location
}

$builderConfig = Get-Content -Raw -Encoding UTF8 (Join-Path $frontendDir 'electron-builder.yml')
if ($builderConfig -notmatch '(?m)^extraResources:') {
  $failures.Add('electron-builder has no extraResources; packaged backend runtime is not configured')
}

if ($failures.Count -gt 0) {
  Write-Host ''
  Write-Host 'Release check failed:' -ForegroundColor Red
  $failures | ForEach-Object { Write-Host ('- ' + $_) -ForegroundColor Red }
  exit 1
}

Write-Host ''
Write-Host 'Release check passed (packaged backend runtime still requires a separate clean-machine verification).' -ForegroundColor Green
