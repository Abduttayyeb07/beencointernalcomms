[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidatePattern('^\d{1,3}(\.\d{1,3}){3}$')]
  [string] $TargetIp,
  [switch] $Connect
)

$ErrorActionPreference = "Stop"
$localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })

if ($localAddresses -contains $TargetIp) {
  throw "$TargetIp belongs to this PC. Run this command on the controlling PC and provide the other target PC's IP address."
}

Write-Host "Testing reachability from this controlling PC to target $TargetIp..."
$ping = Test-Connection -ComputerName $TargetIp -Count 2 -Quiet
$rdp = Test-NetConnection -ComputerName $TargetIp -Port 3389 -InformationLevel Detailed

[pscustomobject]@{
  TargetIp = $TargetIp
  PingSucceeded = $ping
  RdpPort3389Open = $rdp.TcpTestSucceeded
  SourceAddress = $rdp.SourceAddress
} | Format-List

if (-not $ping) {
  throw "The target is not reachable by IP. Check Wi-Fi/VLAN connectivity and MikroTik client isolation."
}

if (-not $rdp.TcpTestSucceeded) {
  throw "The target is reachable, but TCP port 3389 is blocked. Run enable-rdp-private.ps1 as Administrator on the target PC."
}

Write-Host "RDP network checks passed."
if ($Connect) {
  Start-Process mstsc.exe -ArgumentList "/v:$TargetIp"
}
