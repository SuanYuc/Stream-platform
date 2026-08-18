param(
    [string]$HostName = "<YOUR_SERVER_IP>",
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"

function Invoke-JsonGet {
    param([string]$Url)
    $headers = @{}
    if (-not [string]::IsNullOrWhiteSpace($Token)) {
        $headers["Authorization"] = "Bearer $Token"
    }
    Invoke-RestMethod -Method Get -Uri $Url -Headers $headers
}

Write-Host "Testing ports..." -ForegroundColor Cyan
foreach ($port in @(1935, 8888, 8088)) {
    $result = Test-NetConnection -ComputerName $HostName -Port $port -WarningAction SilentlyContinue
    Write-Host ("TCP {0}: {1}" -f $port, $result.TcpTestSucceeded)
}

Write-Host ""
Write-Host "Testing API..." -ForegroundColor Cyan
Invoke-JsonGet -Url "http://$HostName`:8088/api/health" | ConvertTo-Json -Depth 6

Write-Host ""
Write-Host "Querying stream URLs..." -ForegroundColor Cyan
Invoke-JsonGet -Url "http://$HostName`:8088/api/urls?app=live&stream=main" | ConvertTo-Json -Depth 6
