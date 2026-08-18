# Python 导播台接入云推流配置说明

本文说明如何把本地 `Nsy_Broadcasting_platform` 导播台输出到阿里云服务器上的云推流节点。

## 一、当前默认地址

项目已经把默认 RTMP 地址配置为：

```text
rtmp://<YOUR_SERVER_IP>:1935/live/main
```

对应播放地址为：

```text
http://<YOUR_SERVER_IP>:8888/live/main/index.m3u8
```

Go 控制服务地址为：

```text
http://<YOUR_SERVER_IP>:8088
```

这些默认值集中配置在：

```text
nsy_broadcasting_platform/config.py
```

## 二、在导播台界面中使用

启动导播台：

```powershell
python main.py
```

在主界面的输出控制区域，可以看到 `RTMP 地址` 输入框。现在软件启动后会默认填入：

```text
rtmp://<YOUR_SERVER_IP>:1935/live/main
```

操作步骤：

```text
1. 确认云服务器上的 nsy-mediamtx 和 nsy-cloud-stream 服务已启动。
2. 确认阿里云安全组已开放 1935、8888、8088。
3. 在导播台中确认 RTMP 地址为 rtmp://<YOUR_SERVER_IP>:1935/live/main。
4. 点击“开始推流”。
5. 使用 VLC、PotPlayer 或浏览器播放器打开 HLS 地址进行观看。
```

## 三、预览与节目输出子界面

项目中的预览与节目输出子界面也会同步主界面的 RTMP 地址。

如果用户在主界面修改 RTMP 地址，子界面会跟随更新。如果用户在子界面修改 RTMP 地址，主界面也会同步更新。

## 四、推荐编码参数

当前阿里云服务器带宽约为 5 Mbps，建议先使用保守参数：

```text
分辨率：1280x720
帧率：30fps
推流码率：2500k 到 3500k
音频：AAC 128k / 48kHz
编码：自动 CPU+GPU 或优先 GPU/NVENC
```

如果网络稳定，再尝试：

```text
分辨率：1920x1080
帧率：30fps
推流码率：3500k 到 4500k
音频：AAC 128k / 48kHz
```

不建议在 5 Mbps 带宽下使用过高码率，否则节目输出、HLS 播放和中继到阿里云直播都可能出现卡顿。

## 五、端口要求

云服务器必须开放以下端口：

```text
1935：RTMP 推流入口
8888：HLS 播放入口
8088：Go 控制 API
8890：SRT，可选
```

需要同时检查两处：

```text
1. 阿里云控制台安全组入方向规则。
2. Windows Server 系统防火墙。
```

安装脚本会自动处理 Windows 防火墙，但阿里云安全组需要在云控制台中放行。

## 六、验证命令

服务器安装完成后，可以在本地执行：

```powershell
cd cloud_stream_go
.\scripts\smoke_test.ps1 -HostName <YOUR_SERVER_IP> -Token "安装脚本输出的 API token"
```

如果 `1935`、`8888`、`8088` 任意一个为 `False`，说明对应端口还没有打通。

## 七、常见问题

### 1. 点击开始推流后失败

优先检查：

```text
RTMP 地址是否为 rtmp://<YOUR_SERVER_IP>:1935/live/main
云服务器 MediaMTX 服务是否启动
阿里云安全组是否放行 1935
Windows 防火墙是否放行 1935
```

### 2. 推流成功但无法观看 HLS

优先检查：

```text
是否已经开始推流
HLS 地址是否为 http://<YOUR_SERVER_IP>:8888/live/main/index.m3u8
阿里云安全组是否放行 8888
播放器是否支持 HLS
```

### 3. 画面卡顿

优先降低：

```text
推流码率
采集分辨率
采集帧率
ONNX/MediaPipe 智能滤镜使用强度
```

对于 5 Mbps 带宽，推荐先使用 720p 直播。

### 4. 需要推到阿里云直播 CDN

先让本地导播台推到：

```text
rtmp://<YOUR_SERVER_IP>:1935/live/main
```

然后用 Go API 把云服务器上的流中继到阿里云直播推流地址。中继命令见：

```text
cloud_stream_go/README_部署说明.md
```
