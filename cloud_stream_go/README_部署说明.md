# NSY 云推流 Go 服务部署说明

本目录用于在阿里云 Windows Server 上搭建轻量视频服务器，实现“本地导播台推到云端，再由云端播放或中继到阿里云直播”的链路。

文档中的服务器密码只用于你本人通过远程桌面登录服务器。本项目不会把密码写入脚本或配置文件，也不要把密码提交到代码库。

## 一、链路设计

```text
本地导播台
  -> RTMP 推流到云服务器 MediaMTX
  -> 云服务器提供 HLS/RTMP 播放地址
  -> 可选：Go 服务调用 FFmpeg 中继到阿里云直播推流地址
```

默认地址如下：

```text
RTMP 推流地址：rtmp://<YOUR_SERVER_IP>:1935/live/main
HLS 播放地址：http://<YOUR_SERVER_IP>:8888/live/main/index.m3u8
Go API 地址：http://<YOUR_SERVER_IP>:8088
```

## 二、当前服务器情况

已从说明文档读取到以下信息：

```text
公网 IP：<YOUR_SERVER_IP>
系统：Windows Server
登录方式：远程桌面 RDP
```

目前网络探测结果显示：只有 `3389` 远程桌面端口可访问，`1935`、`8888`、`8088` 等业务端口尚未开放。因此暂时不能由本机直接远程自动部署。你需要先通过远程桌面进入服务器，或打开 WinRM/SSH 后再让自动化脚本连接部署。

## 三、服务器部署步骤

1. 使用远程桌面连接服务器：

```text
地址：<YOUR_SERVER_IP>
用户：administrator
密码：使用阿里云说明文档中的密码
```

2. 把整个 `cloud_stream_go` 文件夹复制到服务器，例如放到桌面。

3. 在服务器中以管理员身份打开 PowerShell。

4. 进入目录并执行安装：

```powershell
cd "$env:USERPROFILE\Desktop\cloud_stream_go"
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\install_windows.ps1 -PublicHost <YOUR_SERVER_IP>
```

脚本会自动完成：

```text
安装 Go 编译环境
下载 FFmpeg
下载 MediaMTX
下载 NSSM 服务管理工具
编译 Go 控制服务
注册 Windows 服务
开放 Windows 防火墙端口
输出 RTMP/HLS/API 地址
```

5. 在阿里云控制台安全组中开放入方向 TCP 端口：

```text
1935：RTMP 推流和 RTMP 播放
8888：HLS 播放
8088：Go 控制 API
8890：SRT，暂时可选
```

如果你已经安装并配置了阿里云 CLI，也可以用脚本自动放行端口：

```powershell
.\scripts\open_aliyun_security_group.ps1 `
  -RegionId "你的地域 ID，例如 cn-beijing" `
  -SecurityGroupId "你的安全组 ID"
```

该脚本不会保存 AccessKey。它只调用本机已经配置好的 `aliyun` CLI 凭据。

## 四、本地导播台如何使用

在导播台的 RTMP 推流地址中填写：

```text
rtmp://<YOUR_SERVER_IP>:1935/live/main
```

开始推流后，可以用播放器访问：

```text
http://<YOUR_SERVER_IP>:8888/live/main/index.m3u8
```

如果播放器支持 RTMP，也可以播放：

```text
rtmp://<YOUR_SERVER_IP>:1935/live/main
```

## 五、中继到阿里云直播

如果你已经有阿里云直播推流地址，例如：

```text
rtmp://你的推流域名/live/main?auth_key=xxxx
```

可以通过 Go API 启动中继：

```powershell
$token = "安装脚本最后输出的 API token"
$body = @{
  id = "main"
  app = "live"
  stream = "main"
  target_url = "rtmp://你的推流域名/live/main?auth_key=xxxx"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://<YOUR_SERVER_IP>:8088/api/relay/start" `
  -Headers @{ Authorization = "Bearer $token" } `
  -Body $body `
  -ContentType "application/json"
```

停止中继：

```powershell
$body = @{ id = "main" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri "http://<YOUR_SERVER_IP>:8088/api/relay/stop" `
  -Headers @{ Authorization = "Bearer $token" } `
  -Body $body `
  -ContentType "application/json"
```

查看状态：

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://<YOUR_SERVER_IP>:8088/api/relay/status" `
  -Headers @{ Authorization = "Bearer $token" }
```

## 六、自检

在本机或服务器执行：

```powershell
.\scripts\smoke_test.ps1 -HostName <YOUR_SERVER_IP> -Token "安装脚本输出的 API token"
```

如果端口测试失败，优先检查：

```text
阿里云安全组入方向规则
Windows 防火墙规则
nsy-mediamtx 服务状态
nsy-cloud-stream 服务状态
```

## 七、码率建议

当前服务器公网带宽约为 5 Mbps。为了避免卡顿，建议先使用：

```text
720p：视频码率 2000-2500 kbps，音频 128 kbps
1080p：视频码率 3500-4500 kbps，音频 128 kbps
```

如果多人同时观看 HLS，5 Mbps 带宽会比较紧张。正式直播建议接入阿里云直播 CDN，把云服务器作为接收和中继节点。

## 八、日志位置

默认日志目录：

```text
C:\nsy-cloud-stream\logs
```

常用日志：

```text
nsy-mediamtx.out.log
nsy-mediamtx.err.log
nsy-cloud-stream.out.log
nsy-cloud-stream.err.log
relay-main.log
```
