param(
    [string]$PublicHost = "<YOUR_SERVER_IP>",
    [string]$InstallDir = "C:\nsy-cloud-stream",
    [string]$ApiToken = "",
    [switch]$SkipDownloads
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run PowerShell as Administrator."
    }
}

function New-ApiToken {
    $bytes = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Download-File {
    param(
        [string]$Url,
        [string]$OutFile
    )
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

function Add-FirewallPort {
    param(
        [string]$Name,
        [int]$Port
    )
    if (-not (Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $Name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    }
}

function Expand-ZipToTemp {
    param([string]$ZipFile)
    $dir = Join-Path $env:TEMP ([IO.Path]::GetFileNameWithoutExtension($ZipFile) + "-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Expand-Archive -LiteralPath $ZipFile -DestinationPath $dir -Force
    return $dir
}

function Ensure-Go {
    $goExe = Join-Path $env:ProgramFiles "Go\bin\go.exe"
    if (Test-Path $goExe) {
        return $goExe
    }
    $fromPath = Get-Command go.exe -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }
    if ($SkipDownloads) {
        throw "Go is not installed and -SkipDownloads was set."
    }

    Write-Step "Installing Go toolchain"
    $index = Invoke-RestMethod -Uri "https://go.dev/dl/?mode=json"
    $stable = $index | Select-Object -First 1
    $asset = $stable.files | Where-Object { $_.filename -match "windows-amd64\.msi$" } | Select-Object -First 1
    if (-not $asset) {
        throw "Could not find Go windows-amd64 MSI from go.dev."
    }
    $msi = Join-Path $DownloadsDir $asset.filename
    Download-File -Url ("https://go.dev/dl/" + $asset.filename) -OutFile $msi
    Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart" -Wait

    if (-not (Test-Path $goExe)) {
        throw "Go installation finished, but go.exe was not found."
    }
    return $goExe
}

function Ensure-FFmpeg {
    $ffmpegExe = Join-Path $BinDir "ffmpeg.exe"
    if (Test-Path $ffmpegExe) {
        return $ffmpegExe
    }
    if ($SkipDownloads) {
        throw "ffmpeg.exe is missing and -SkipDownloads was set."
    }

    Write-Step "Installing FFmpeg"
    $zip = Join-Path $DownloadsDir "ffmpeg-release-essentials.zip"
    Download-File -Url "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    $tmp = Expand-ZipToTemp -ZipFile $zip
    $found = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $found) {
        throw "ffmpeg.exe was not found in downloaded package."
    }
    Copy-Item -LiteralPath $found.FullName -Destination $ffmpegExe -Force
    return $ffmpegExe
}

function Ensure-MediaMTX {
    $exe = Join-Path $BinDir "mediamtx.exe"
    if (Test-Path $exe) {
        return $exe
    }
    if ($SkipDownloads) {
        throw "mediamtx.exe is missing and -SkipDownloads was set."
    }

    Write-Step "Installing MediaMTX"
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/bluenviron/mediamtx/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -match "windows_amd64.*\.zip$" } | Select-Object -First 1
    if (-not $asset) {
        throw "Could not find MediaMTX windows_amd64 release asset."
    }
    $zip = Join-Path $DownloadsDir $asset.name
    Download-File -Url $asset.browser_download_url -OutFile $zip
    $tmp = Expand-ZipToTemp -ZipFile $zip
    $found = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "mediamtx.exe" | Select-Object -First 1
    if (-not $found) {
        throw "mediamtx.exe was not found in downloaded package."
    }
    Copy-Item -LiteralPath $found.FullName -Destination $exe -Force
    return $exe
}

function Ensure-NSSM {
    $exe = Join-Path $BinDir "nssm.exe"
    if (Test-Path $exe) {
        return $exe
    }
    if ($SkipDownloads) {
        throw "nssm.exe is missing and -SkipDownloads was set."
    }

    Write-Step "Installing NSSM service wrapper"
    $zip = Join-Path $DownloadsDir "nssm-2.24.zip"
    Download-File -Url "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
    $tmp = Expand-ZipToTemp -ZipFile $zip
    $found = Get-ChildItem -LiteralPath $tmp -Recurse -Filter "nssm.exe" |
        Where-Object { $_.FullName -match "\\win64\\" } |
        Select-Object -First 1
    if (-not $found) {
        throw "nssm.exe win64 was not found in downloaded package."
    }
    Copy-Item -LiteralPath $found.FullName -Destination $exe -Force
    return $exe
}

function Install-NSSMService {
    param(
        [string]$Name,
        [string]$App,
        [string[]]$ServiceArgs
    )
    $existing = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        & $NssmExe stop $Name 2>$null | Out-Null
        & $NssmExe remove $Name confirm 2>$null | Out-Null
        Start-Sleep -Seconds 1
    }
    & $NssmExe install $Name $App @ServiceArgs | Out-Null
    & $NssmExe set $Name AppDirectory $InstallDir | Out-Null
    & $NssmExe set $Name AppStdout (Join-Path $LogDir "$Name.out.log") | Out-Null
    & $NssmExe set $Name AppStderr (Join-Path $LogDir "$Name.err.log") | Out-Null
    & $NssmExe set $Name AppRotateFiles 1 | Out-Null
    & $NssmExe set $Name AppRotateBytes 10485760 | Out-Null
    & $NssmExe set $Name Start SERVICE_AUTO_START | Out-Null
    & $NssmExe start $Name | Out-Null
}

Assert-Admin

$PackageRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BinDir = Join-Path $InstallDir "bin"
$LogDir = Join-Path $InstallDir "logs"
$DownloadsDir = Join-Path $InstallDir "downloads"

Write-Step "Preparing folders"
New-Item -ItemType Directory -Force -Path $InstallDir, $BinDir, $LogDir, $DownloadsDir | Out-Null

$FFmpegExe = Ensure-FFmpeg
$MediaMTXExe = Ensure-MediaMTX
$NssmExe = Ensure-NSSM

Write-Step "Preparing Go control service"
$OutExe = Join-Path $InstallDir "nsy-cloud-stream.exe"
$BundledExe = Join-Path $PackageRoot "cloud-stream-server.exe"
if (Test-Path $BundledExe) {
    Copy-Item -LiteralPath $BundledExe -Destination $OutExe -Force
} else {
    $GoExe = Ensure-Go
    Push-Location $PackageRoot
    try {
        & $GoExe build -o $OutExe ".\cmd\cloud-stream-server"
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path $OutExe)) {
    throw "Go control service missing: $OutExe was not generated."
}

Write-Step "Writing server config"
if ([string]::IsNullOrWhiteSpace($ApiToken)) {
    $ApiToken = New-ApiToken
}
$config = [ordered]@{
    listen = ":8088"
    public_host = $PublicHost
    api_token = $ApiToken
    rtmp_port = 1935
    hls_port = 8888
    ffmpeg_path = $FFmpegExe
    log_dir = $LogDir
}
$configPath = Join-Path $InstallDir "config.json"
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
Copy-Item -LiteralPath (Join-Path $PackageRoot "configs\mediamtx.yml") -Destination (Join-Path $InstallDir "mediamtx.yml") -Force

Write-Step "Opening Windows Firewall ports"
Add-FirewallPort -Name "NSY Cloud Stream RTMP 1935" -Port 1935
Add-FirewallPort -Name "NSY Cloud Stream HLS 8888" -Port 8888
Add-FirewallPort -Name "NSY Cloud Stream API 8088" -Port 8088
Add-FirewallPort -Name "NSY Cloud Stream SRT 8890" -Port 8890

Write-Step "Installing Windows services"
Install-NSSMService -Name "nsy-mediamtx" -App $MediaMTXExe -ServiceArgs @((Join-Path $InstallDir "mediamtx.yml"))
Install-NSSMService -Name "nsy-cloud-stream" -App $OutExe -ServiceArgs @("-config", $configPath)

Write-Step "Installed"
Write-Host "Push RTMP : rtmp://$PublicHost`:1935/live/main"
Write-Host "Play HLS  : http://$PublicHost`:8888/live/main/index.m3u8"
Write-Host "API       : http://$PublicHost`:8088/api/health"
Write-Host "API token : $ApiToken"
Write-Host ""
Write-Host "Remember to open Alibaba Cloud Security Group inbound TCP ports: 1935, 8888, 8088, and optional 8890."


