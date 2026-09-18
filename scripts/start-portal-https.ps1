[CmdletBinding()]
param(
  [switch] $Restart
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$serverPath = Join-Path $projectRoot "apps\api\portal_server.py"
$dataPath = Join-Path $projectRoot "data"
$certPath = Join-Path $dataPath "certs"

$listeners = @(
  @{
    Port = 443
    Certificate = Join-Path $certPath "connect.beenco.local.crt"
    Key = Join-Path $certPath "connect.beenco.local.key"
    OutputLog = Join-Path $dataPath "portal-server.out.log"
    ErrorLog = Join-Path $dataPath "portal-server.err.log"
  },
  @{
    Port = 4173
    Certificate = Join-Path $certPath "localhost.crt"
    Key = Join-Path $certPath "localhost.key"
    OutputLog = Join-Path $dataPath "portal-server-4173.out.log"
    ErrorLog = Join-Path $dataPath "portal-server-4173.err.log"
  }
)

if (-not (Test-Path -LiteralPath $serverPath)) {
  throw "Portal server not found at $serverPath"
}

foreach ($listener in $listeners) {
  if (-not (Test-Path -LiteralPath $listener.Certificate) -or -not (Test-Path -LiteralPath $listener.Key)) {
    throw "TLS certificate or key is missing for port $($listener.Port). Expected $($listener.Certificate) and $($listener.Key)"
  }
}

$python = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
  $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
  throw "Python was not found. Install Python or add py.exe/python.exe to PATH."
}

function Get-PortalListener {
  param([int] $Port)

  $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $connection) {
    return $null
  }

  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
  [pscustomobject]@{
    Port = $Port
    ProcessId = $connection.OwningProcess
    Name = $process.Name
    CommandLine = $process.CommandLine
  }
}

function Test-PortalHealth {
  param([int] $Port)

  $result = & curl.exe -k -sS --max-time 3 "https://localhost:$($Port)/api/health" 2>$null
  return $LASTEXITCODE -eq 0 -and $result -match '"ok"\s*:\s*true'
}

foreach ($listener in $listeners) {
  $existing = Get-PortalListener -Port $listener.Port
  if (-not $existing) {
    continue
  }

  $isPortal = $existing.CommandLine -like "*portal_server.py*" -or (
    $existing.Name -like "python*" -and (Test-PortalHealth -Port $listener.Port)
  )
  if (-not $isPortal) {
    throw "Port $($listener.Port) is already used by process $($existing.ProcessId), which is not the portal server."
  }

  if ($Restart) {
    Stop-Process -Id $existing.ProcessId -Force
  } else {
    Write-Host "Portal is already listening on port $($listener.Port) (PID $($existing.ProcessId))."
  }
}

if ($Restart) {
  Start-Sleep -Seconds 1
}

foreach ($listener in $listeners) {
  if (Get-PortalListener -Port $listener.Port) {
    continue
  }

  $environment = @{
    HOST = "0.0.0.0"
    PORT = [string] $listener.Port
    SSL_CERT_FILE = $listener.Certificate
    SSL_KEY_FILE = $listener.Key
  }

  foreach ($entry in $environment.GetEnumerator()) {
    Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
  }

  Start-Process `
    -FilePath $python `
    -ArgumentList $serverPath `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $listener.OutputLog `
    -RedirectStandardError $listener.ErrorLog `
    -WindowStyle Hidden | Out-Null
}

$deadline = (Get-Date).AddSeconds(15)
do {
  $ready = @($listeners | Where-Object { Get-PortalListener -Port $_.Port }).Count -eq $listeners.Count
  if (-not $ready) {
    Start-Sleep -Milliseconds 500
  }
} until ($ready -or (Get-Date) -ge $deadline)

if (-not $ready) {
  throw "The portal did not start on every HTTPS port. Review the logs in $dataPath."
}

$healthChecks = @(
  "https://localhost/api/health",
  "https://localhost:4173/api/health",
  "https://connect.beenco.local/api/health"
)

$healthPayloads = @()
foreach ($url in $healthChecks) {
  $result = & curl.exe -k -sS --max-time 5 $url
  if ($LASTEXITCODE -ne 0 -or $result -notmatch '"ok"\s*:\s*true') {
    throw "Health check failed: $url"
  }
  try {
    $healthPayloads += ($result | ConvertFrom-Json)
  } catch {
  }
  Write-Host "Healthy: $url"
}

$accessUrls = @("https://connect.beenco.local", "https://localhost:4173")
foreach ($payload in $healthPayloads) {
  if ($payload.accessUrls) {
    foreach ($accessUrl in $payload.accessUrls) {
      $accessUrls += ($accessUrl -replace ":443$", "")
    }
  }
}
$accessUrls = $accessUrls | Where-Object { $_ } | Select-Object -Unique

Write-Host ""
Write-Host "Beenco Connect is running:"
foreach ($accessUrl in $accessUrls) {
  Write-Host "  $accessUrl"
}
