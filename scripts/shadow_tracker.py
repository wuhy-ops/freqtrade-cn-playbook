#!/usr/bin/env python3
"""30 天影子跟踪（不产生真实订单）。

跟踪两套已关闭研究的假设，积累前向证据：
  1. B1：10 币池，90 日动量前 5 等权，月度再平衡；
          BTC 收盘 > 日线 EMA200 才持仓，否则空仓。
  2. RSI2：10 币池，收盘 > EMA200 且 RSI(2) < 10 入场；
          RSI(2) > 70 或 -8% 止损出场；最多 3 个仓位，每仓 100 USDT。

每日 08:12（日线收盘后）运行，写入 shadow_state.json / shadow_history.jsonl，
并推送一条简短 Telegram。2026-12-13 起发送一次 30 天复盘。
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "/opt/freqtrade"
CFG_PATH = os.path.join(BASE, "user_data/config.json")
STATE_PATH = os.path.join(BASE, "user_data/shadow_state.json")
HIST_PATH = os.path.join(BASE, "user_data/shadow_history.jsonl")
LOG_PATH = os.path.join(BASE, "user_data/logs/shadow_tracker.log")

COINS = ["BTC", "ETH", "BNB", "XRP", "DOGE", "ADA", "LTC", "LINK", "ATOM", "SOL"]
FEE = 0.002
INITIAL = 1000.0
RSI_POS_SIZE = 100.0
MAX_RSI_POS = 3
TOP_N = 5
REVIEW_DATE = "2026-12-13"


def log(msg):
    line = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "shadow-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def klines(symbol, limit=320):
    url = "https://api.binance.com/api/v3/klines?symbol=%s&interval=1d&limit=%d" % (symbol + "USDT", limit)
    return get_json(url)


def completed_klines(rows):
    now = datetime.now(timezone.utc)
    if not rows:
        return rows
    last_open = datetime.fromtimestamp(rows[-1][0] / 1000.0, tz=timezone.utc)
    if last_open.date() >= now.date():
        rows = rows[:-1]
    return rows


def ema(values, period):
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values, period=2):
    if len(values) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
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


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "b1": {"cash": INITIAL, "holdings": {}, "last_rebalance_month": None, "equity": INITIAL},
        "rsi2": {"cash": INITIAL, "positions": {}, "closed": [], "equity": INITIAL},
        "review_sent": False,
    }


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def send_telegram(text):
    try:
        cfg = json.load(open(CFG_PATH, encoding="utf-8"))
        tg = cfg.get("telegram") or {}
        token, chat_id = tg.get("token"), tg.get("chat_id")
        if not token or not chat_id:
            return
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        log("telegram failed: %r" % exc)


def main():
    state = load_state()
    data = {}
    for coin in COINS:
        rows = completed_klines(klines(coin, 320))
        if len(rows) < 220:
            log("%s insufficient data (%d)" % (coin, len(rows)))
            continue
        closes = [float(r[4]) for r in rows]
        data[coin] = {
            "date": datetime.fromtimestamp(rows[-1][0] / 1000.0, tz=timezone.utc).date().isoformat(),
            "close": closes[-1],
            "ema200": ema(closes, 200),
            "rsi2": rsi(closes, 2),
            "ret90": (closes[-1] / closes[-91] - 1.0) if len(closes) > 91 else None,
        }

    if "BTC" not in data:
        log("BTC data missing, abort")
        return

    today = data["BTC"]["date"]
    month = today[:7]
    btc_above = data["BTC"]["close"] > data["BTC"]["ema200"]
    prices = {c: data[c]["close"] for c in data}

    # ---------- B1 shadow ----------
    b1 = state["b1"]
    if b1.get("last_rebalance_month") != month:
        equity_before = b1["cash"] + sum(amt * prices[c] for c, amt in b1["holdings"].items() if c in prices)
        # 卖出旧持仓（统一扣一次卖出费）
        cash = equity_before * (1.0 - FEE) if b1["holdings"] else equity_before
        holdings = {}
        ranked = sorted(
            [c for c in COINS if c in data and data[c]["ret90"] is not None],
            key=lambda c: data[c]["ret90"],
            reverse=True,
        )[:TOP_N]
        if btc_above and ranked:
            per = cash / (len(ranked) * (1.0 + FEE))
            for c in ranked:
                amount = per / prices[c]
                cash -= per * (1.0 + FEE)
                holdings[c] = amount
        b1.update({
            "cash": cash,
            "holdings": holdings,
            "last_rebalance_month": month,
            "top5": ranked,
            "btc_above": btc_above,
        })
    b1["btc_above"] = btc_above
    b1["equity"] = b1["cash"] + sum(amt * prices[c] for c, amt in b1["holdings"].items() if c in prices)

    # ---------- RSI2 shadow ----------
    r = state["rsi2"]
    positions = r["positions"]
    cash = r["cash"]
    signals = {"entries": [], "exits": []}

    for c in list(positions.keys()):
        if c not in data:
            continue
        d = data[c]
        p = positions[c]
        if d["rsi2"] > 70 or d["close"] <= p["entry_price"] * 0.92:
            proceeds = p["amount"] * d["close"] * (1.0 - FEE)
            pnl = proceeds - p["cost"]
            cash += proceeds
            r["closed"].append({
                "coin": c,
                "entry_date": p["entry_date"],
                "exit_date": d["date"],
                "pnl": round(pnl, 2),
                "reason": "rsi" if d["rsi2"] > 70 else "stop",
            })
            del positions[c]
            signals["exits"].append("%s %+.2f" % (c, pnl))

    for c in COINS:
        if len(positions) >= MAX_RSI_POS:
            break
        if c in positions or c not in data:
            continue
        d = data[c]
        if d["close"] > d["ema200"] and d["rsi2"] < 10 and cash >= RSI_POS_SIZE * (1.0 + FEE):
            amount = RSI_POS_SIZE / d["close"]
            cost = RSI_POS_SIZE * (1.0 + FEE)
            cash -= cost
            positions[c] = {
                "entry_date": d["date"],
                "entry_price": d["close"],
                "amount": amount,
                "cost": cost,
            }
            signals["entries"].append(c)

    r["cash"] = cash
    r["equity"] = cash + sum(p["amount"] * data[c]["close"] for c, p in positions.items() if c in data)
    wins = sum(1 for x in r["closed"] if x["pnl"] > 0)
    r["stats"] = {
        "closed": len(r["closed"]),
        "wins": wins,
        "winrate": (wins / len(r["closed"]) * 100.0) if r["closed"] else 0.0,
        "pnl": round(sum(x["pnl"] for x in r["closed"]), 2),
    }

    # ---------- history ----------
    hist = {
        "date": today,
        "b1_equity": round(b1["equity"], 2),
        "b1_holdings": sorted(b1["holdings"].keys()),
        "btc_above_200d": btc_above,
        "rsi2_equity": round(r["equity"], 2),
        "rsi2_positions": sorted(positions.keys()),
        "rsi2_closed": len(r["closed"]),
        "signals": signals,
    }
    try:
        with open(HIST_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(hist, ensure_ascii=False) + "\n")
    except Exception:
        pass

    save_state(state)

    # ---------- daily telegram ----------
    lines = ["【影子跟踪 %s】" % today[5:]]
    if btc_above:
        lines.append("B1：BTC 在 200D 上方；持仓 %s" % (
            "、".join(sorted(b1["holdings"].keys())) if b1["holdings"] else "空仓"))
    else:
        lines.append("B1：BTC 在 200D 下方；空仓")
    lines.append("B1 影子净值：%.2f（%+.2f%%）" % (
        b1["equity"], (b1["equity"] / INITIAL - 1.0) * 100.0))

    if positions:
        parts = []
        for c, p in sorted(positions.items()):
            if c in data:
                pct = (data[c]["close"] / p["entry_price"] - 1.0) * 100.0
                parts.append("%s %+.1f%%" % (c, pct))
        lines.append("RSI2：持仓 %d 个（%s）" % (len(positions), "，".join(parts)))
    else:
        lines.append("RSI2：空仓")
    lines.append("RSI2 已平仓 %d 笔，胜率 %.0f%%，累计 %+.2f" % (
        r["stats"]["closed"], r["stats"]["winrate"], r["stats"]["pnl"]))
    if signals["entries"]:
        lines.append("今日买入信号：%s" % "、".join(signals["entries"]))
    if signals["exits"]:
        lines.append("今日卖出信号：%s" % "、".join(signals["exits"]))

    send_telegram("\n".join(lines))

    # ---------- 30-day review ----------
    if today >= REVIEW_DATE and not state.get("review_sent"):
        review = [
            "【30 天影子跟踪复盘】%s ~ %s" % ("2026-11-13", today),
            "B1：影子净值 %.2f，收益 %+.2f%%" % (
                b1["equity"], (b1["equity"] / INITIAL - 1.0) * 100.0),
            "RSI2：已平仓 %d 笔，胜率 %.0f%%，累计 %+.2f" % (
                r["stats"]["closed"], r["stats"]["winrate"], r["stats"]["pnl"]),
            "当前 RSI2 持仓：%s" % ("、".join(sorted(positions.keys())) or "空仓"),
            "说明：这是影子证据，不构成实盘建议。",
        ]
        send_telegram("\n".join(review))
        state["review_sent"] = True
        save_state(state)
        log("30-day review sent")

    log("done b1=%.2f rsi2=%.2f" % (b1["equity"], r["equity"]))


if __name__ == "__main__":
    main()
