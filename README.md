# NVIDIA GPU Monitor

一个轻量级的 GPU 指标采集与告警脚本，基于 `nvidia-smi` 实现。

## 功能特点
- 支持采集温度、利用率、显存、功耗和 ECC 错误。
- 支持自定义告警阈值，并返回系统退出码（0 正常 / 1 告警）。
- 自动生成带时间戳的 JSON 报告，避免覆盖历史数据。
- 跨平台支持（处理 Windows 控制台编码问题）。

## 使用方法
\`\`\`bash
python gpu_monitor.py
python gpu_monitor.py --temp-threshold 85 --mem-threshold 95
python gpu_monitor.py --output report.json --verbose
