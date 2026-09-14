#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nvidia-smi GPU 指标采集与告警脚本
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone


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
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gpu_count": len(all_gpus),
        "alert_count": len(all_alerts),
        "status": "ALERT" if all_alerts else "OK",
        "gpus": all_gpus,
        "alerts": all_alerts,
    }


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


def get_args():
    parser = argparse.ArgumentParser(description="GPU 指标采集与告警")
    parser.add_argument("--temp-threshold", type=float, default=80,
                        help="温度告警阈值，默认 80 度")
    parser.add_argument("--mem-threshold", type=float, default=90,
                        help="显存使用率告警阈值，默认 90%")
    parser.add_argument("--output", default=None,
                        help="把 JSON 报告写到这个文件")
    parser.add_argument("--verbose", action="store_true",
                        help="多打印 ECC 等信息")
    return parser.parse_args()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = get_args()
    lines = get_raw_output()

    all_gpus = []
    all_alerts = []

    for line in lines:
        gpu = parse_line(line)
        alerts = check_alerts(gpu, args.temp_threshold, args.mem_threshold)
        all_gpus.append(gpu)
        all_alerts.extend(alerts)

    report = build_report(all_gpus, all_alerts)
    print_report(report, args.verbose)

    # 生成带时间戳的文件名，避免覆盖历史报告
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.output:
        base, ext = os.path.splitext(args.output)
        output_path = f"{base}_{timestamp}{ext or '.json'}"
    else:
        output_path = f"gpu_report_{timestamp}.json"

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("报告已写入：" + output_path)

    return 1 if all_alerts else 0


if __name__ == "__main__":
    sys.exit(main())
