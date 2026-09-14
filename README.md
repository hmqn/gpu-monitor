# NVIDIA GPU Monitor

一个轻量级的 GPU 指标采集与告警脚本，基于 `nvidia-smi` 实现。

## 功能特点
- 支持采集温度、利用率、显存、功耗和 ECC 错误。
- 支持自定义告警阈值，并返回系统退出码（0 正常 / 1 告警）。
- 自动生成带时间戳的 JSON 报告，避免覆盖历史数据。
- 报告统一收进当前目录下的 `gpu_reports/` 文件夹，目录不存在时自动创建，已存在则直接复用。
- 跨平台支持（处理 Windows 控制台编码问题）。
- 支持 `config.json` 配置文件：启动时先读配置，命令行参数可临时覆盖。

## 使用方法
```bash
python gpu_monitor.py
python gpu_monitor.py --temp-threshold 85 --mem-threshold 95
python gpu_monitor.py --output report.json --verbose
```

## 报告存放位置

不带参数直接运行时，报告写入**当前工作目录**下的 `gpu_reports/` 文件夹：

```
当前目录/
├── gpu_monitor.py
├── config.json
└── gpu_reports/
    ├── gpu_report_20260914_1340_07.json
    └── gpu_report_20260914_1442_31.json
```

- 目录不存在时会自动创建，并在输出里提示：`报告目录不存在，已创建：...`。
- 目录已存在（包括上次运行留下的）就直接复用，不会重复创建，也不会报错。
- 想换目录：`--output-dir logs` 或改配置里的 `output_dir`；想平铺到当前目录：`--output-dir .`。
- 显式给了 `--output`（或配置里设了 `output`）时，按你给的位置写，`output_dir` 不参与。

## 配置文件

脚本每次启动都会先读取**与脚本同目录**的 `config.json`，其中的值作为默认值。

配置优先级（从低到高）：

```
内置默认值  <  config.json  <  命令行参数
```

也就是说：命令行上显式给出的参数只在本次运行生效，不会改动配置文件。

`config.json` 支持的字段：

| 字段 | 类型 | 内置默认值 | 说明 |
| --- | --- | --- | --- |
| `temp_threshold` | 数字 | `80.0` | 温度告警阈值（摄氏度） |
| `mem_threshold` | 数字 | `90.0` | 显存使用率告警阈值（百分比） |
| `output` | 字符串或 `null` | `null` | 报告输出路径；`null` 表示自动生成带时间戳的文件名并放进 `output_dir` |
| `output_dir` | 字符串 | `"gpu_reports"` | 未指定 `output` 时的报告目录（相对当前工作目录） |
| `verbose` | `true` / `false` | `false` | 是否打印 ECC 等信息 |

`config.json` 示例：

```json
{
  "temp_threshold": 85.0,
  "mem_threshold": 95.0,
  "output": null,
  "output_dir": "gpu_reports",
  "verbose": true
}
```

以 `_` 开头的字段会被当作注释静默忽略，方便手写备注：

```json
{
  "_note": "机房 A 的采集卡，夏天阈值放宽一点",
  "temp_threshold": 85
}
```

配置文件缺失、JSON 损坏或者字段值非法时，脚本只会打印 `[警告]` 并回退到默认值，不会中断采集。

## 相关参数

| 参数 | 说明 |
| --- | --- |
| `--temp-threshold` | 温度告警阈值（默认 80 度） |
| `--mem-threshold` | 显存使用率告警阈值（默认 90%） |
| `--output` | 把 JSON 报告写到这个文件（给了它就不再使用 `output_dir`） |
| `--output-dir` | 报告存放目录，默认当前目录下的 `gpu_reports` |
| `--verbose` / `--no-verbose` | 开启 / 关闭 ECC 打印，可用于临时覆盖配置里的 `verbose: true` |
| `--config PATH` | 指定配置文件路径，默认脚本同目录下的 `config.json` |
| `--show-config` | 打印当前生效的设置及其来源，然后退出 |
| `--save-config` | 把当前生效的设置永久写回配置文件，然后退出 |

### 查看当前配置

```bash
python gpu_monitor.py --show-config
```

每一项后面会标出来源（`命令行` / `配置文件` / `内置默认值`）：

```
========================================================
当前生效的配置（优先级：命令行 > 配置文件 > 内置默认值）
========================================================
  温度告警阈值 : 85 C  [命令行]
  显存告警阈值 : 95 %  [配置文件]
  输出文件路径 : 自动生成带时间戳的文件  [内置默认值]
  报告存放目录 : gpu_reports  [内置默认值]
  打印 ECC 信息 : 是  [配置文件]
--------------------------------------------------------
配置文件：D:\...\nvidia-gpu-monitor\config.json
========================================================
```

### 把当前参数保存为默认值

```bash
python gpu_monitor.py --temp-threshold 85 --mem-threshold 95 --verbose --save-config
```

执行后当前生效的设置会被写入 `config.json`（先写临时文件再替换，避免写坏原文件），
然后脚本直接退出，不会采集数据。之后不带参数运行就会沿用这套设置：

```bash
python gpu_monitor.py                 # 使用 config.json 里保存的 85 / 95 / verbose
python gpu_monitor.py --temp-threshold 70   # 本次用 70，配置文件仍是 85
```

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 正常，无告警（或执行了 `--show-config` / `--save-config`） |
| `1` | 检测到告警 |
| `2` | 执行错误（找不到 `nvidia-smi`、超时、写配置文件失败等） |
