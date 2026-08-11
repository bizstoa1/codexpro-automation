[CmdletBinding()]
param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }),
  [string]$ConfigPath = ''
)

$ErrorActionPreference = 'Stop'
$CodexRoot = [IO.Path]::GetFullPath($CodexHome)
if (!$ConfigPath) { $ConfigPath = Join-Path $CodexRoot 'config/codexpro-devspace-bootstrap.json' }
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
$LogRoot = Join-Path $CodexRoot 'logs/codexpro-devspace'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ("bootstrap-{0}.log" -f (Get-Date -Format 'yyyy-MM'))

function Write-BootstrapLog([string]$Message) {
  Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message)
}

$Mutex = New-Object Threading.Mutex($false, 'Local\CodexProDevSpaceBootstrap')
$Acquired = $false
try {
  $Acquired = $Mutex.WaitOne(0)
  if (!$Acquired) { exit 0 }
  Write-BootstrapLog 'Bootstrap started.'
  if (!(Test-Path -LiteralPath $ConfigPath)) { throw "Bootstrap config missing: $ConfigPath" }
  $Config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if ([string]$Config.schema -ne 'codexpro.devspace-bootstrap/v1') { throw 'Unsupported bootstrap config schema.' }
  $Python = [string]$Config.python_path
  if (!$Python) {
    $PythonCommand = Get-Command python.exe,python -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$PythonCommand) { throw 'Python is unavailable.' }
    $Python = $PythonCommand.Source
  }
  if (!(Test-Path -LiteralPath $Python)) { throw "Python is unavailable: $Python" }
  $Helper = Join-Path $CodexRoot 'skills/chatgpt-workspace-setup/scripts/devspace_tailscale_setup.py'
  if (!(Test-Path -LiteralPath $Helper)) { throw "DevSpace recovery helper missing: $Helper" }
  if (Test-Path -LiteralPath 'C:\Program Files\Tailscale') {
    $env:PATH = 'C:\Program Files\Tailscale;' + $env:PATH
  }
  $Arguments = @($Helper, 'recover')
  foreach ($Root in @($Config.roots)) { $Arguments += @('--root', [string]$Root) }
  $Arguments += @('--hostname', [string]$Config.hostname)
  if ($Config.local_port) { $Arguments += @('--local-port', [string]$Config.local_port) }
  if ($Config.public_port) { $Arguments += @('--public-port', [string]$Config.public_port) }

  for ($Attempt = 1; $Attempt -le 6; $Attempt++) {
    $PreviousPreference = $ErrorActionPreference
    try {
      $ErrorActionPreference = 'Continue'
      & $Python @Arguments *> $null
      $ExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $PreviousPreference
    }
    if ($ExitCode -eq 0) {
      Write-BootstrapLog "DevSpace and Funnel are healthy (attempt $Attempt)."
      exit 0
    }
    Write-BootstrapLog "Recovery attempt $Attempt failed with exit code $ExitCode."
    if ($Attempt -lt 6) { Start-Sleep -Seconds 15 }
  }
  throw 'DevSpace recovery retries exhausted.'
} catch {
  Write-BootstrapLog ("Bootstrap failed: {0}" -f $_.Exception.Message)
  exit 1
} finally {
  if ($Acquired) { $Mutex.ReleaseMutex() }
  $Mutex.Dispose()
}
