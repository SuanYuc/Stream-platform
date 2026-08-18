param(
    [Parameter(Mandatory = $true)]
    [string]$RegionId,

    [Parameter(Mandatory = $true)]
    [string]$SecurityGroupId,

    [string]$SourceCidrIp = "0.0.0.0/0",

    [int[]]$Ports = @(1935, 8888, 8088, 8890)
)

$ErrorActionPreference = "Stop"

function Assert-AliyunCli {
    $cmd = Get-Command aliyun -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "aliyun CLI was not found. Install Alibaba Cloud CLI and run 'aliyun configure' first."
    }
}

function Add-AliyunTcpRule {
    param([int]$Port)
    $range = "$Port/$Port"
    Write-Host "Opening TCP $range on $SecurityGroupId"
    $output = & aliyun ecs AuthorizeSecurityGroup `
        --RegionId $RegionId `
        --SecurityGroupId $SecurityGroupId `
        --IpProtocol tcp `
        --PortRange $range `
        --SourceCidrIp $SourceCidrIp `
        --Policy accept `
        --Priority 1 2>&1

    $text = ($output | Out-String)
    if ($LASTEXITCODE -ne 0) {
        if ($text -match "InvalidPermission\\.Duplicate|already exists|重复") {
            Write-Host "TCP $range already exists, skipped."
            return
        }
        throw $text
    }
}

Assert-AliyunCli
foreach ($port in $Ports) {
    Add-AliyunTcpRule -Port $port
}

Write-Host ""
Write-Host "Done. Opened TCP ports: $($Ports -join ', ')"
Write-Host "Security group: $SecurityGroupId"
Write-Host "Source CIDR: $SourceCidrIp"
