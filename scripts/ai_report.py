#!/usr/bin/env python3
"""每日 AI 晨报：读取 Freqtrade 数据 + 币安行情，调用 DeepSeek 生成分析，推送到 Telegram。

只使用 Python 标准库，无需安装依赖。
Key 从 /opt/freqtrade/deepseek.key 读取（首行，sk- 开头）。
"""

import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import market_data

BASE = "/opt/freqtrade"
CFG_PATH = os.path.join(BASE, "user_data/config.json")
DB_PATH = os.path.join(BASE, "user_data/tradesv3.sqlite")
KEY_PATH = os.path.join(BASE, "deepseek.key")
LOG_PATH = os.path.join(BASE, "user_data/logs/ai_report.log")
CST = timezone(timedelta(hours=8))


def log(msg):
    line = datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def http_json(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_klines(symbol, interval="1h", limit=120):
    url = "https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (symbol, interval, limit)
    rows = http_json(url, timeout=20)
    return [
        {
            "open_time": int(r[0]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]


def ema(values, period):
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, period=14):
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def market_snapshot(symbol):
    ks = get_klines(symbol, "1h", 120)
    closes = [k["close"] for k in ks]
    last = closes[-1]
    ref_24h = closes[-25] if len(closes) > 25 else closes[0]
    chg24 = (last / ref_24h - 1.0) * 100.0
    vol24 = sum(k["volume"] for k in ks[-24:])
    vol_prev = sum(k["volume"] for k in ks[-48:-24]) if len(ks) >= 48 else vol24
    vol_chg = ((vol24 / vol_prev - 1.0) * 100.0) if vol_prev else 0.0
    return {
        "symbol": symbol[:-4] + "/USDT",
        "price": last,
        "chg24": chg24,
        "vol_chg": vol_chg,
        "ema20": ema(closes, 20),
        "ema50": ema(closes, 50),
        "rsi": rsi(closes),
        "high24": max(k["high"] for k in ks[-24:]),
        "low24": min(k["low"] for k in ks[-24:]),
    }


def load_regime():
    try:
        with open(os.path.join(BASE, "user_data/ai_regime.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def parse_dt(value):
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def db_snapshot():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    open_trades = [dict(r) for r in con.execute(
        "select pair, is_short, open_date, open_rate, amount, stake_amount, max_rate, min_rate "
        "from trades where is_open=1"
    ).fetchall()]
    closed = [dict(r) for r in con.execute(
        "select pair, close_date, close_profit_abs, close_profit, exit_reason, enter_tag "
        "from trades where is_open=0 order by close_date desc limit 50"
    ).fetchall()]
    con.close()
    return open_trades, closed


def summarize_trades(closed):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    day = [t for t in closed if (parse_dt(t["close_date"]) or now) >= now - timedelta(hours=24)]
    week = [t for t in closed if (parse_dt(t["close_date"]) or now) >= now - timedelta(days=7)]

    def agg(items):
        pnl = sum(float(t["close_profit_abs"] or 0) for t in items)
        wins = sum(1 for t in items if float(t["close_profit_abs"] or 0) > 0)
        rate = (wins / len(items) * 100.0) if items else 0.0
        return len(items), pnl, rate

    return agg(day), agg(week), len(closed), sum(float(t["close_profit_abs"] or 0) for t in closed)


def build_prompt(cfg, open_trades, closed, markets, md):
    day_n, day_pnl, day_wr = summarize_trades(closed)[0]
    week_n, week_pnl, week_wr = summarize_trades(closed)[1]
    all_n, all_pnl = summarize_trades(closed)[2], summarize_trades(closed)[3]
    initial = float(cfg.get("dry_run_wallet", 1000))
    balance = initial + all_pnl

    lines = []
    now_str = datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M")
    lines.append("以下是交易机器人在 %s（北京时间）的数据，请生成每日晨报。" % now_str)
    lines.append("")
    lines.append("【账户】")
    lines.append("模式：%s" % ("模拟盘 dry-run" if cfg.get("dry_run") else "实盘"))
    lines.append("初始资金：%.2f USDT" % initial)
    lines.append("当前估算余额：%.2f USDT（含已实现盈亏，未计未平仓浮动）" % balance)
    lines.append("累计已实现盈亏：%+.2f USDT，共 %d 笔已平仓交易" % (all_pnl, all_n))
    lines.append("最近24小时：%d 笔平仓，盈亏 %+.2f USDT，胜率 %.0f%%" % (day_n, day_pnl, day_wr))
    lines.append("最近7天：%d 笔平仓，盈亏 %+.2f USDT，胜率 %.0f%%" % (week_n, week_pnl, week_wr))
    lines.append("")

    lines.append("【当前持仓】")
    if open_trades:
        for t in open_trades:
            side = "空头" if t.get("is_short") else "多头"
            lines.append(
                " - %s %s：开仓价 %.4f，数量 %.6f，投入 %.2f USDT，期间最高 %.4f / 最低 %.4f"
                % (t["pair"], side, float(t["open_rate"]), float(t["amount"]),
                   float(t["stake_amount"]), float(t["max_rate"] or 0), float(t["min_rate"] or 0))
            )
    else:
        lines.append(" - 当前空仓")
    lines.append("")

    lines.append("【最近平仓交易】")
    if closed:
        for t in closed[:5]:
            lines.append(
                " - %s：盈亏 %+.2f USDT（%+.2f%%），原因 %s，平仓时间 %s"
                % (t["pair"], float(t["close_profit_abs"] or 0),
                   float(t["close_profit"] or 0) * 100.0,
                   t["exit_reason"] or "-", str(t["close_date"])[:16])
            )
    else:
        lines.append(" - 暂无已平仓交易")
    lines.append("")

    lines.append("【市场数据（Binance 1h K线）】")
    for m in markets:
        rsi_txt = "%.1f" % m["rsi"] if m["rsi"] is not None else "N/A"
        lines.append(
            "%s：现价 %.2f，24h涨跌 %+.2f%%，24h成交量较前日 %+.1f%%，"
            "EMA20 %.2f，EMA50 %.2f，RSI14 %s，24h最高 %.2f / 最低 %.2f"
            % (m["symbol"], m["price"], m["chg24"], m["vol_chg"],
               m["ema20"], m["ema50"], rsi_txt, m["high24"], m["low24"])
        )
    lines.append("")
    lines.append(market_data.format_for_prompt(md))
    lines.append("")

    regime = load_regime()
    lines.append("【AI 风控状态】")
    if regime:
        lines.append(
            "状态：%s（置信度 %s），生成时间 %s"
            % (regime.get("regime", "unknown"), regime.get("confidence", "-"),
               regime.get("generated_at", "-"))
        )
        lines.append("说明：%s" % regime.get("summary", ""))
        reasons = regime.get("reasons") or []
        if reasons:
            lines.append("原因：%s" % "；".join(str(r) for r in reasons))
    else:
        lines.append("状态文件不存在或不可读（策略按默认放行处理）")
    lines.append("")
    lines.append(
        "请输出纯文本晨报，中文，450字以内，结构如下：\n"
        "1. 账户概况（2行）\n"
        "2. 市场结构（各币种走势 + 市场广度 + 衍生品 + 情绪，只基于上述数据，不得编造新闻或数据）\n"
        "3. 持仓点评（空仓就写“当前空仓，无持仓风险”）；同时说明 AI 风控状态是 risk_on/neutral/risk_off 中的哪一种\n"
        "4. 风险提示（1-2条）\n"
        "5. 一句话总结\n"
        "最后一行固定写：以上为数据分析，不构成投资建议。\n"
        "不要给买入/卖出/目标价建议，不要使用 markdown 表格。"
    )
    return "\n".join(lines)


def ask_deepseek(api_key, prompt):
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一名严谨的加密货币市场分析助手，只基于给定数据做客观描述，禁止编造，禁止投资建议。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1200,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def send_telegram(cfg, text):
    tg = cfg.get("telegram") or {}
    token, chat_id = tg.get("token"), tg.get("chat_id")
    if not token or not chat_id:
        raise RuntimeError("config.json 缺少 telegram.token / chat_id")
    if len(text) > 3900:
        text = text[:3900] + "\n...(内容过长已截断)"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError("Telegram 返回失败: %s" % data)


def main():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        if not os.path.exists(KEY_PATH):
            raise RuntimeError("缺少 %s，请先写入 DeepSeek API Key" % KEY_PATH)
        with open(KEY_PATH, encoding="utf-8") as f:
            api_key = f.read().strip()
        if not api_key.startswith("sk-"):
            raise RuntimeError("DeepSeek Key 格式不对（应以 sk- 开头）")

        open_trades, closed = db_snapshot()
        markets = [
            market_snapshot("BTCUSDT"), market_snapshot("ETHUSDT"), market_snapshot("ZECUSDT"),
            market_snapshot("SOLUSDT"), market_snapshot("XRPUSDT"), market_snapshot("DOGEUSDT"),
            market_snapshot("BNBUSDT"), market_snapshot("LINKUSDT"),
        ]
        md = market_data.collect()
        prompt = build_prompt(cfg, open_trades, closed, markets, md)
        log("prompt built, %d chars" % len(prompt))

        report = ask_deepseek(api_key, prompt)
        log("deepseek ok, %d chars" % len(report))

        header = "【AI 每日晨报】%s\n\n" % datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M")
        send_telegram(cfg, header + report)
        log("telegram sent")
    except Exception as exc:
        log("ERROR: %r" % exc)
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            send_telegram(cfg, "⚠️ AI 晨报生成失败：%s" % str(exc)[:300])
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
