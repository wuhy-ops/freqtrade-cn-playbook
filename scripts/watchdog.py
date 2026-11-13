#!/usr/bin/env python3
"""Freqtrade 服务看门狗。

每 5 分钟检查一次：
  - systemd 服务 freqtrade 是否 active
  - Docker 容器是否在运行
  - 日志中的 Bot heartbeat 是否新鲜（默认 10 分钟）
  - 磁盘使用率是否超过 90%

异常时推送 Telegram；恢复时也推送一条。
同一问题 1 小时内不重复轰炸（冷却）。

用法：
  python3 watchdog.py         正常检查
  python3 watchdog.py --test  发送一条测试消息
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CFG_PATH = "/opt/freqtrade/user_data/config.json"
LOG_PATH = "/opt/freqtrade/user_data/logs/watchdog.log"
STATE_PATH = "/opt/freqtrade/user_data/watchdog_state.json"
FT_LOG = "/opt/freqtrade/user_data/logs/freqtrade.log"

HEARTBEAT_MAX_AGE = 600      # 秒
DISK_MAX_PCT = 90.0
ALERT_COOLDOWN = 3600        # 秒


def log(msg):
    line = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_telegram(text):
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        tg = cfg.get("telegram") or {}
        token, chat_id = tg.get("token"), tg.get("chat_id")
        if not token or not chat_id:
            log("telegram 未配置，跳过发送")
            return
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        log("telegram 发送失败: %r" % exc)


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception:
        return None


def service_active():
    r = run(["systemctl", "is-active", "freqtrade"])
    return bool(r) and r.stdout.strip() == "active"


def service_enabled():
    r = run(["systemctl", "is-enabled", "freqtrade"])
    return bool(r) and r.stdout.strip() in ("enabled", "static", "indirect")


def container_running():
    r = run(["docker", "inspect", "-f", "{{.State.Running}}", "freqtrade"])
    return bool(r) and r.stdout.strip() == "true"


def heartbeat_age():
    try:
        size = os.path.getsize(FT_LOG)
        with open(FT_LOG, "rb") as f:
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", "ignore")
        last = None
        for line in data.splitlines():
            if "Bot heartbeat" in line:
                last = line
        if not last:
            return None
        ts = datetime.strptime(last[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception as exc:
        log("读取心跳失败: %r" % exc)
        return None


def disk_pct():
    u = shutil.disk_usage("/")
    return u.used / u.total * 100.0


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
    except Exception:
        pass


def main():
    if "--test" in sys.argv:
        send_telegram("✅ 看门狗测试消息：Telegram 告警通道正常。")
        log("test message sent")
        return

    problems = []

    svc = service_active()
    if not svc:
        # 研发已收口：freqtrade 正常状态就是停止且 disabled。
        # 只有“未运行但 enabled”才视为异常（例如崩溃后没起来）。
        if service_enabled():
            problems.append("freqtrade 服务未运行，但处于 enabled 状态（可能崩溃）")
        else:
            log("freqtrade 已停止且 disabled，按预期停止处理，跳过机器人检查")
    else:
        if not container_running():
            problems.append("freqtrade 容器未运行（docker inspect Running=false）")
        age = heartbeat_age()
        if age is None:
            problems.append("日志中找不到 Bot heartbeat")
        elif age > HEARTBEAT_MAX_AGE:
            problems.append("心跳已停止 %.0f 分钟（超过阈值 %.0f 分钟）" % (age / 60, HEARTBEAT_MAX_AGE / 60))

    dp = disk_pct()
    if dp > DISK_MAX_PCT:
        problems.append("磁盘使用率 %.1f%%（超过 %.0f%%）" % (dp, DISK_MAX_PCT))

    now = time.time()
    state = load_state()
    prev = state.get("status", "unknown")
    status = "fail" if problems else "ok"

    if problems:
        should_alert = prev != "fail" or now - float(state.get("last_alert", 0)) > ALERT_COOLDOWN
        detail = "\n".join("- " + p for p in problems)
        log("FAIL: " + "; ".join(problems))
        if should_alert:
            send_telegram("⚠️ Freqtrade 异常\n\n" + detail + "\n\n检查命令：\nssh root@<服务器IP>\nsystemctl status freqtrade\ntail -50 /opt/freqtrade/user_data/logs/freqtrade.log")
            state["last_alert"] = now
    else:
        log("OK")
        if prev == "fail":
            send_telegram("✅ Freqtrade 已恢复正常。\n心跳正常，服务运行中。")

    state["status"] = status
    state["checked_at"] = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)


if __name__ == "__main__":
    main()
