[CmdletBinding()]
param(
  [switch] $SetActiveNetworkPrivate,
  [string] $TrustedSubnet = "10.30.0.0/24"
)

$ErrorActionPreference = "Stop"
$isAdministrator = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdministrator) {
  throw "Administrator access is required. Open PowerShell as Administrator, then run: powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`""
}

$windowsName = (Get-CimInstance Win32_OperatingSystem).Caption
if ($windowsName -match "Home") {
  throw "$windowsName cannot accept incoming Microsoft Remote Desktop connections. Upgrade the target PC to Windows Pro, Enterprise, or Education, or use a different remote-support product."
}

$activeProfiles = @(Get-NetConnectionProfile | Where-Object {
  $_.IPv4Connectivity -ne "Disconnected" -or $_.IPv6Connectivity -ne "Disconnected"
})
$publicProfiles = @($activeProfiles | Where-Object { $_.NetworkCategory -eq "Public" })

if ($publicProfiles.Count -gt 0) {
  if ($SetActiveNetworkPrivate) {
    foreach ($profile in $publicProfiles) {
      Set-NetConnectionProfile -InterfaceIndex $profile.InterfaceIndex -NetworkCategory Private
    }
  } else {
    $interfaces = ($publicProfiles | ForEach-Object { $_.InterfaceAlias }) -join ", "
    Write-Warning "Active network profile is Public: $interfaces. The custom firewall rules below remain restricted to $TrustedSubnet."
  }
}

Set-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -Value 0
Set-Service -Name TermService -StartupType Manual
Restart-Service -Name TermService -Force
Start-Sleep -Seconds 2

netsh advfirewall firewall set rule name="Remote Desktop - User Mode (TCP-In)" new enable=yes profile=domain,private | Out-Host
netsh advfirewall firewall set rule name="Remote Desktop - User Mode (UDP-In)" new enable=yes profile=domain,private | Out-Host

$ruleNames = @(
  "Beenco RDP from trusted LAN (TCP)",
  "Beenco RDP from trusted LAN (UDP)"
)
Get-NetFirewallRule -DisplayName $ruleNames -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
  -DisplayName $ruleNames[0] `
  -Direction Inbound `
  -Action Allow `
  -Protocol TCP `
  -LocalPort 3389 `
  -RemoteAddress $TrustedSubnet `
  -Profile Any | Out-Null
New-NetFirewallRule `
  -DisplayName $ruleNames[1] `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalPort 3389 `
  -RemoteAddress $TrustedSubnet `
  -Profile Any | Out-Null

Write-Host "Remote Desktop enabled. Public-profile access is restricted to trusted subnet $TrustedSubnet."
Get-NetConnectionProfile |
  Select-Object Name, InterfaceAlias, NetworkCategory, IPv4Connectivity |
  Format-Table -AutoSize
Get-NetFirewallRule -DisplayName $ruleNames |
  Get-NetFirewallAddressFilter |
  Select-Object InstanceID, RemoteAddress |
  Format-Table -AutoSize
Get-Service TermService | Format-Table Name, Status, StartType
Get-NetTCPConnection -LocalPort 3389 -State Listen -ErrorAction SilentlyContinue |
  Format-Table LocalAddress, LocalPort, State, OwningProcess

$targetAddresses = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | ForEach-Object {
  $_.IPv4Address.IPAddress
})
if ($targetAddresses.Count -gt 0) {
  Write-Host ""
  Write-Host "Do not test 127.0.0.1. Loopback is intentionally excluded by the trusted-LAN firewall rule."
  Write-Host "From a different PC on the trusted LAN, connect to one of these target addresses:"
  foreach ($address in $targetAddresses) {
    Write-Host "  Test-NetConnection $address -Port 3389"
    Write-Host "  mstsc.exe /v:$address"
  }
}
