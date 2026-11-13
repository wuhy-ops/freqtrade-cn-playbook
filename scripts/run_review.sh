#!/usr/bin/env bash
# 每日策略体检：重新回测 -> AI 诊断 -> Telegram 推送
# 由 ai-review.timer 每天 09:00 触发，也可以手动执行。

set -uo pipefail

LOG="/opt/freqtrade/user_data/logs/ai_review_run.log"

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

echo ""
echo "============================================================"
echo "=== start $(date '+%F %T %Z') ==="

notify() {
    python3 - "$1" <<'PY'
import json, sys, urllib.parse, urllib.request
try:
    cfg = json.load(open("/opt/freqtrade/user_data/config.json", encoding="utf-8"))
    tg = cfg.get("telegram") or {}
    token, chat_id = tg.get("token"), tg.get("chat_id")
    if token and chat_id:
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": sys.argv[1][:3900]}).encode("utf-8")
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
        urllib.request.urlopen(req, timeout=30).read()
except Exception as exc:
    print("notify failed: %r" % exc)
PY
}

# 1) 重新回测并导出结果
echo "--- backtest ---"
if ! docker run --rm \
        -v /opt/freqtrade/user_data:/freqtrade/user_data \
        freqtradeorg/freqtrade:stable backtesting \
        --config /freqtrade/user_data/config.json \
        --strategy MyStrategy \
        --export trades \
        --timerange 20251101-; then
    echo "!!! backtest failed"
    notify "⚠️ 每日策略体检失败：回测阶段出错。请登录服务器查看 /opt/freqtrade/user_data/logs/ai_review_run.log"
    exit 1
fi

# 2) AI 诊断（脚本内部会推送 Telegram）
echo "--- review ---"
if ! python3 /opt/freqtrade/ai_strategy_review.py; then
    echo "!!! review failed"
    notify "⚠️ 每日策略体检失败：AI 诊断阶段出错。请登录服务器查看 /opt/freqtrade/user_data/logs/ai_review_run.log"
    exit 1
fi

echo "=== done $(date '+%F %T %Z') ==="
