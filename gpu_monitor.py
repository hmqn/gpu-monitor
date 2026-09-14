#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nvidia-smi GPU 指标采集与告警脚本

配置优先级（从低到高）：
    内置默认值  <  config.json  <  命令行参数

每次启动都会先读取 config.json，其中的值作为默认值；
命令行上显式给出的参数只在本次运行生效，不会改动配置文件。
如果需要把当前参数固化成新的默认值，加上 --save-config。
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta


FIELDS = {
    "name":         "name",
    "temperature":  "temperature.gpu",
    "utilization":  "utilization.gpu",
    "memory_used":  "memory.used",
    "memory_total": "memory.total",
    "power_draw":   "power.draw",
    "ecc_ce":       "ecc.errors.corrected.aggregate.total",
    "ecc_ue":       "ecc.errors.uncorrected.aggregate.total",
}


# ---------------------------------------------------------------- 配置定义 --

CONFIG_FILENAME = "config.json"

# 未指定 --output 时，报告统一放到当前目录下的这个子目录里
DEFAULT_OUTPUT_DIR = "gpu_reports"

# 可写入配置文件的字段及内置默认值（最底层，优先级最低）
HARD_DEFAULTS = {
    "temp_threshold": 80.0,
    "mem_threshold":  90.0,
    "output":         None,
    "output_dir":     DEFAULT_OUTPUT_DIR,
    "verbose":        False,
}

# 打印配置时使用的中文名称
KEY_LABELS = {
    "temp_threshold": "温度告警阈值",
    "mem_threshold":  "显存告警阈值",
    "output":         "输出文件路径",
    "output_dir":     "报告存放目录",
    "verbose":        "打印 ECC 信息",
}

SOURCE_CLI = "命令行"
SOURCE_CONFIG = "配置文件"
SOURCE_DEFAULT = "内置默认值"


# ---------------------------------------------------------------- 配置读写 --

def default_config_path():
    """默认配置文件：与脚本放在同一目录，保证在任何工作目录下都能读到。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILENAME)


def validate_config_value(key, value):
    """校验单个配置项，返回 (是否合法, 归一化后的值, 出错说明)。"""
    if key in ("temp_threshold", "mem_threshold"):
        # bool 是 int 的子类，这里要显式排除，避免 true/false 被当成阈值
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, "需要数字"
        return True, float(value), ""

    if key == "output":
        if value is None:
            return True, None, ""
        if not isinstance(value, str):
            return False, None, "需要字符串或 null"
        text = value.strip()
        return True, (text or None), ""

    if key == "output_dir":
        if not isinstance(value, str):
            return False, None, "需要字符串"
        text = value.strip()
        if not text:
            return False, None, "需要非空字符串"
        return True, text, ""

    if key == "verbose":
        if not isinstance(value, bool):
            return False, None, "需要 true 或 false"
        return True, value, ""

    return False, None, "未知字段"


def load_config(path):
    """
    读取配置文件。

    返回 (配置字典, 提示信息列表)。
    文件不存在不算错误；内容损坏或字段非法时只警告并跳过该字段，
    绝不让脚本因为配置问题直接崩溃。
    """
    if not os.path.exists(path):
        return {}, []

    try:
        # 用 utf-8-sig 读取：记事本 / PowerShell 保存的 UTF-8 文件常带 BOM，
        # 带不带 BOM 都能正常解析
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return {}, ["配置文件 JSON 格式错误，已忽略并改用默认值：" + path
                    + "（" + str(exc) + "）"]
    except OSError as exc:
        return {}, ["配置文件读取失败，已忽略并改用默认值：" + path
                    + "（" + str(exc) + "）"]

    if not isinstance(data, dict):
        return {}, ["配置文件内容不是 JSON 对象，已忽略并改用默认值：" + path]

    config = {}
    warnings = []
    for key, value in data.items():
        if isinstance(key, str) and key.startswith("_"):
            # 下划线开头的字段当注释用，静默忽略
            continue
        if key not in HARD_DEFAULTS:
            warnings.append("配置文件里的字段无法识别，已忽略：" + str(key))
            continue
        ok, normalized, reason = validate_config_value(key, value)
        if not ok:
            warnings.append("配置项 " + str(key) + " 的值不合法（" + reason
                            + "），已忽略并改用默认值")
            continue
        config[key] = normalized

    return config, warnings


def save_config(path, settings):
    """把设置写回配置文件。先写临时文件再替换，避免写一半损坏原文件。"""
    data = {key: settings[key] for key in HARD_DEFAULTS}

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def resolve_settings(cli_values, config):
    """
    三层合并配置。

    cli_values 来自 argparse，未在命令行给出的项为 None，
    因此可以准确区分“命令行没写”和“命令行写了默认值”。
    返回 (生效设置, 每一项的来源)。
    """
    settings = {}
    sources = {}

    for key, hard_default in HARD_DEFAULTS.items():
        cli_value = cli_values.get(key)
        if cli_value is not None:
            settings[key] = cli_value
            sources[key] = SOURCE_CLI
        elif key in config:
            settings[key] = config[key]
            sources[key] = SOURCE_CONFIG
        else:
            settings[key] = hard_default
            sources[key] = SOURCE_DEFAULT

    return settings, sources


# ---------------------------------------------------------------- 配置展示 --

def format_number(value):
    """80.0 打印成 80，避免默认值显示得别扭。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_setting(key, value):
    if key == "temp_threshold":
        return format_number(value) + " C"
    if key == "mem_threshold":
        return format_number(value) + " %"
    if key == "output":
        return value if value else "自动生成带时间戳的文件"
    if key == "verbose":
        return "是" if value else "否"
    return str(value)


def print_config(settings, sources, config_path, config_exists):
    line = "=" * 56
    print(line)
    print("当前生效的配置（优先级：命令行 > 配置文件 > 内置默认值）")
    print(line)
    for key in HARD_DEFAULTS:
        print("  " + KEY_LABELS[key] + " : " + format_setting(key, settings[key])
              + "  [" + sources[key] + "]")
    print("-" * 56)
    print("配置文件：" + config_path
          + ("" if config_exists else "（不存在，当前使用内置默认值）"))
    print(line)


# ------------------------------------------------------------ nvidia-smi --

def get_raw_output():
    query = ",".join(FIELDS.values())

    command = [
        "nvidia-smi",
        "--query-gpu=" + query,
        "--format=csv,noheader,nounits",
        ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError:
        print("[错误] 找不到 nvidia-smi 命令，确认已安装 NVIDIA 驱动")
        sys.exit(2)
    except subprocess.TimeoutExpired:
        print("[错误] nvidia-smi 执行超时，GPU 可能异常")
        sys.exit(2)

    if result.returncode != 0:
        print("[错误] nvidia-smi 执行失败：", result.stderr.strip())
        sys.exit(2)

    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return lines


def parse_line(line):
    raw_values = [piece.strip() for piece in line.split(",")]
    keys = list(FIELDS.keys())

    if len(raw_values) != len(keys):
        print("[错误] nvidia-smi 返回的列数不对")
        print("  预期", len(keys), "列，实际", len(raw_values), "列")
        print("  原始行：", line)
        sys.exit(2)

    gpu = {}
    for key, raw in zip(keys, raw_values):
        gpu[key] = to_number(raw)

    return gpu


def to_number(text):
    if text in ("N/A", "[N/A]", "N/A ", ""):
        return None

    cleaned = text.strip("[]").strip()
    if cleaned.upper() in ("N/A", "NA", "") or "not supported" in cleaned.lower():
        return None

    try:
        return int(cleaned)
    except ValueError:
        pass

    try:
        return float(cleaned)
    except ValueError:
        return text


def check_alerts(gpu, temp_threshold, mem_threshold):
    alerts = []

    # 温度
    temp = gpu["temperature"]
    if temp is not None and temp > temp_threshold:
        alerts.append("温度 " + str(temp) + "C 超过阈值 " + str(temp_threshold) + "C")

    # 显存使用率
    used = gpu["memory_used"]
    total = gpu["memory_total"]
    if used is not None and total:
        percent = used / total * 100
        gpu["memory_percent"] = round(percent, 2)
        if percent > mem_threshold:
            alerts.append(
                "显存使用率 " + str(round(percent, 2)) + "%"
                + "（" + str(used) + "/" + str(total) + " MiB）"
                + " 超过阈值 " + str(mem_threshold) + "%"
            )
    else:
        gpu["memory_percent"] = None

    # ECC 错误（消费级显卡通常为 N/A，数据中心卡适用）
    ecc_ue = gpu["ecc_ue"]
    if ecc_ue is not None and ecc_ue > 0:
        alerts.append("检测到 " + str(ecc_ue) + " 个不可纠正 ECC 错误(UE)，建议隔离该卡")

    ecc_ce = gpu["ecc_ce"]
    if ecc_ce is not None and ecc_ce > 0:
        alerts.append("检测到 " + str(ecc_ce) + " 个可纠正 ECC 错误(CE)，需持续观察")

    return alerts


def build_report(all_gpus, all_alerts):
    # 使用北京时间（UTC+8），格式为 YYYY-MM-DD HH:MM:SS
    beijing_time = datetime.now(timezone(timedelta(hours=8)))

    return {
        "timestamp": beijing_time.strftime("%Y-%m-%d %H:%M:%S"),
        "gpu_count": len(all_gpus),
        "alert_count": len(all_alerts),
        "status": "ALERT" if all_alerts else "OK",
        "gpus": all_gpus,
        "alerts": all_alerts,
    }


def build_output_path(settings, timestamp):
    """
    算出本次报告要写入的路径。

    显式配置了 output 时按原样使用，目录由用户自己决定；
    没有配置时统一收进 output_dir（默认 gpu_reports/），
    免得把当前目录堆满 json 文件。
    """
    if settings["output"]:
        base, ext = os.path.splitext(settings["output"])
        return f"{base}_{timestamp}{ext or '.json'}"

    filename = "gpu_report_" + timestamp + ".json"
    return os.path.join(settings["output_dir"], filename)


def ensure_directory(path):
    """确保文件所在目录存在。目录已存在就直接复用，不重复创建。"""
    directory = os.path.dirname(os.path.abspath(path))
    if os.path.isdir(directory):
        return None
    os.makedirs(directory, exist_ok=True)
    return directory


def show(value, unit=""):
    if value is None:
        return "N/A"
    return str(value) + unit


def print_report(report, verbose):
    line = "=" * 64
    print(line)
    print("GPU 监控报告  " + report["timestamp"])
    print("主机 GPU 数量：" + str(report["gpu_count"]))
    print(line)

    for gpu in report["gpus"]:
        print("显卡：" + str(gpu["name"]))
        print("  温度    ：" + show(gpu["temperature"], " C"))
        print("  利用率  ：" + show(gpu["utilization"], " %"))
        print("  显存    ：" + show(gpu["memory_used"], " MiB")
              + " / " + show(gpu["memory_total"], " MiB"))
        print("  功耗    ：" + show(gpu["power_draw"], " W"))

        if verbose:
            print("  ECC CE  ：" + show(gpu["ecc_ce"]))
            print("  ECC UE  ：" + show(gpu["ecc_ue"]))
        print()

    print(line)
    if report["status"] == "OK":
        print("状态：正常，没有告警")
    else:
        print("状态：发现 " + str(report["alert_count"]) + " 条告警")
        for alert in report["alerts"]:
            print("  [告警] " + alert)
    print(line)


# ---------------------------------------------------------------- 参数解析 --

def build_parser():
    parser = argparse.ArgumentParser(
        description="GPU 指标采集与告警",
        epilog="配置优先级：命令行参数 > config.json > 内置默认值；"
               "用 --save-config 可把当前设置永久写入配置文件。",
    )

    # 所有配置项默认值都是 None，用来区分“命令行没给”和“命令行给了值”
    parser.add_argument("--temp-threshold", type=float, default=None,
                        help="温度告警阈值，默认 80 度")
    parser.add_argument("--mem-threshold", type=float, default=None,
                        help="显存使用率告警阈值，默认 90%%")
    parser.add_argument("--output", default=None,
                        help="把 JSON 报告写到这个文件")
    parser.add_argument("--output-dir", dest="output_dir", default=None,
                        help="未指定 --output 时的报告目录，默认当前目录下的 "
                             + DEFAULT_OUTPUT_DIR)
    parser.add_argument("--verbose", dest="verbose", action="store_true", default=None,
                        help="多打印 ECC 等信息")
    parser.add_argument("--no-verbose", dest="verbose", action="store_false", default=None,
                        help="本次不打印 ECC（用于临时覆盖配置文件里的 verbose: true）")

    parser.add_argument("--config", default=None,
                        help="配置文件路径，默认脚本同目录下的 config.json")
    parser.add_argument("--show-config", action="store_true",
                        help="显示当前生效的设置并退出")
    parser.add_argument("--save-config", action="store_true",
                        help="把当前生效的设置永久写回配置文件并退出")
    return parser


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = build_parser().parse_args()
    cli_values = vars(args)

    # 1. 读取配置文件并合并出本次运行真正生效的设置
    config_path = os.path.abspath(args.config) if args.config else default_config_path()
    config_exists = os.path.exists(config_path)
    config, warnings = load_config(config_path)
    settings, sources = resolve_settings(cli_values, config)

    for warning in warnings:
        print("[警告] " + warning)

    # 2. 如果使用了自定义命令行参数，指出被临时覆盖的项
    overridden = [KEY_LABELS[key] for key in HARD_DEFAULTS
                  if cli_values.get(key) is not None]
    if overridden:
        print(">>> 检测到命令行参数（仅本次生效）：" + "、".join(overridden))

    # 3. 保存配置：写入后退出，不采集数据
    if args.save_config:
        try:
            save_config(config_path, settings)
        except OSError as exc:
            print("[错误] 写配置文件失败：" + str(exc))
            return 2
        print("已将当前设置写入配置文件：" + config_path)
        print_config(settings, sources, config_path, True)
        return 0

    # 4. 显示配置：打印后退出
    if args.show_config:
        print_config(settings, sources, config_path, config_exists)
        return 0

    lines = get_raw_output()

    all_gpus = []
    all_alerts = []

    for line in lines:
        gpu = parse_line(line)
        alerts = check_alerts(gpu, settings["temp_threshold"], settings["mem_threshold"])
        all_gpus.append(gpu)
        all_alerts.extend(alerts)

    report = build_report(all_gpus, all_alerts)
    print_report(report, settings["verbose"])

    # 生成带时间戳的文件名，避免覆盖历史报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M_%S")
    output_path = build_output_path(settings, timestamp)

    created_dir = ensure_directory(output_path)
    if created_dir:
        print("报告目录不存在，已创建：" + created_dir)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("报告已写入：" + output_path)

    return 1 if all_alerts else 0


if __name__ == "__main__":
    sys.exit(main())
