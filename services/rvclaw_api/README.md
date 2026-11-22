# rvclaw_api

服务层职责：承接 `M2/M3/M4` 状态与结果，向 `Studio` 提供本地接口。

火情检测复用 Astra 相机流：切换到 `astra_fire` 后，`/api/cameras` 返回连续帧确认后的 `inference.fire_alert` 和 `inference.fire_count`；首次确认告警时保存一张带检测框的图片，`/api/fire-evidence` 列出记录，`/api/fire-evidence/<id>/image.jpg` 返回图片。火焰模型配置在 `configs/thresholds/fire_yolov8.yaml`，权重由使用者放入模型缓存，不提交到 Git。

## 预期输入

- 任务状态
- 设备状态
- 识别结果
- 告警结果

## 预期输出

- 供 `Studio` 使用的 HTTP / WebSocket 接口
- 任务控制入口
- 历史任务查询入口
