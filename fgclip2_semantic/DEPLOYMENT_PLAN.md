# FG-CLIP2 ONNX 部署方案

## 当前已完成

已将张润祎项目训练后的 FG-CLIP2 checkpoint 导出为两个 ONNX encoder：

- `fgclip2_image_encoder.onnx`：直播画面/抽帧图像编码器，输出 L2-normalized `image_embeds`
- `fgclip2_text_encoder.onnx`：语义查询文本编码器，输出 L2-normalized `text_embeds`

匹配得分：

```python
score = image_embeds @ text_embeds.T
```

导出校验结果见 `metadata.json`，ONNX 与 PyTorch 的误差在 `1e-6` 量级。

## 推荐接入方式

1. 在 `Nsy_Broadcasting_platform` 中新增语义推理模块，例如：
   `nsy_broadcasting_platform/core/semantic_onnx.py`
2. 初始化时加载两个 `onnxruntime.InferenceSession`：
   - `onnx_models/fgclip2_semantic/fgclip2_image_encoder.onnx`
   - `onnx_models/fgclip2_semantic/fgclip2_text_encoder.onnx`
3. 用户输入语义查询后，只运行一次文本 encoder，并缓存 `text_embeds`。
4. pyav/ffmpeg 管线对每个候选镜头按固定间隔抽帧，例如每 `0.5s` 或每 `15` 帧。
5. 抽帧后使用 `image_processing_fgclip2.py` / `image_processor` 配置生成：
   - `pixel_values`: `[1, 128, 768]`, float32
   - `pixel_attention_mask`: `[1, 128]`, int64
   - `spatial_shapes`: `[1, 2]`, int64
6. 运行图像 encoder，得到 `image_embeds`。
7. 计算得分，做时间平滑：
   - `smoothed = 0.7 * old + 0.3 * current`
   - 加切换冷却时间，避免镜头抖动。
8. 将最高得分镜头映射到 NSY 的 scene/layer，调用现有导播切换逻辑。

## 直播性能建议

- 文本 embedding 必须缓存，查询词不变时不要重复跑文本模型。
- 图像 encoder 不建议逐帧全量跑；先用 `0.5s` 采样间隔验证效果。
- 若 CPU 延迟过高，可设置 `NSY_ONNX_USE_CUDA=1` 并安装 GPU 版 onnxruntime。
- 当前导出固定 `batch=1`、`max_num_patches=128`、文本长度 `64`，适合直播导播逐路/逐帧快速筛选。

## 注意

导出脚本在 ONNX 导出路径中关闭了位置编码插值的 antialias，以避开 PyTorch `aten::_upsample_bilinear2d_aa` 无法导出到 ONNX opset 17 的问题。该改动只影响 ONNX 导出脚本，不改变原始训练和 PyTorch 推理代码。
