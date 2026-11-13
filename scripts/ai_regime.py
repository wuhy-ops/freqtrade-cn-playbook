#!/usr/bin/env python3
"""每日 AI 市场状态判断（方案 A）。

每天运行一次，调用 DeepSeek 输出 risk_on / neutral / risk_off，
写入 /opt/freqtrade/user_data/ai_regime.json 供交易策略读取。
状态变化时通过 Telegram 通知。

设计原则：
  - 每天只调用一次，成本极低
  - 结论一天内不变，可审计、可复盘
  - 脚本失败时不写文件，策略会按“文件过期 → 默认放行”降级
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import market_data

BASE = "/opt/freqtrade"
CFG_PATH = os.path.join(BASE, "user_data/config.json")
KEY_PATH = os.path.join(BASE, "deepseek.key")
OUT_PATH = os.path.join(BASE, "user_data/ai_regime.json")
LOG_PATH = os.path.join(BASE, "user_data/logs/ai_regime.log")
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


def klines(symbol, interval="1d", limit=200):
    url = "https://api.binance.com/api/v3/klines?symbol=%s&interval=%s&limit=%d" % (symbol, interval, limit)
    rows = http_json(url, timeout=20)
    return [
        {
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
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def atr_pct(ks, period=14):
    if len(ks) <= period:
        return None
    trs = []
    for i in range(1, len(ks)):
        h, l, pc = ks[i]["high"], ks[i]["low"], ks[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr / ks[-1]["close"] * 100.0


def snapshot(symbol):
    ks = klines(symbol, "1d", 200)
    closes = [k["close"] for k in ks]
    last = closes[-1]
    chg7 = (last / closes[-8] - 1.0) * 100.0 if len(closes) > 8 else 0.0
    vol_now = sum(k["volume"] for k in ks[-7:])
    vol_prev = sum(k["volume"] for k in ks[-14:-7]) if len(ks) > 14 else vol_now
    vol_chg = ((vol_now / vol_prev - 1.0) * 100.0) if vol_prev else 0.0
    return {
        "symbol": symbol[:-4] + "/USDT",
        "price": last,
        "chg7": chg7,
        "ema20": ema(closes, 20),
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "rsi": rsi(closes),
        "atr_pct": atr_pct(ks),
        "vol_chg7": vol_chg,
    }


def funding_rate(symbol="BTCUSDT"):
    try:
        d = http_json("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=%s" % symbol, timeout=15)
        return float(d.get("lastFundingRate", 0.0)) * 100.0
    except Exception:
        return None


def build_prompt(markets, funding, md):
    lines = ["请只根据以下数据判断未来 24 小时的加密市场风险状态，输出严格 JSON。", ""]
    lines.append("【日线数据】")
    for m in markets:
        rsi_txt = "%.1f" % m["rsi"] if m["rsi"] is not None else "N/A"
        atr_txt = "%.2f%%" % m["atr_pct"] if m["atr_pct"] is not None else "N/A"
        lines.append(
            "%s：现价 %.2f，7日涨跌 %+.2f%%，EMA20 %.2f / EMA50 %.2f / EMA200 %.2f，"
            "RSI14 %s，ATR14 占价格 %s，7日成交量较前7日 %+.1f%%"
            % (m["symbol"], m["price"], m["chg7"], m["ema20"], m["ema50"],
               m["ema200"], rsi_txt, atr_txt, m["vol_chg7"])
        )
    lines.append("")
    if funding is not None:
        lines.append("【BTC 永续合约资金费率】%.4f%%（正值为多头付钱给空头）" % funding)
    else:
        lines.append("【BTC 永续合约资金费率】获取失败")
    lines.append("")
    lines.append(market_data.format_for_prompt(md))
    lines.append("")
    lines.append("输出字段（只输出 JSON，不要任何解释文字、不要 markdown 代码块）：")
    lines.append('{"regime":"risk_on|neutral|risk_off","confidence":0-100,')
    lines.append(' "summary":"一句话总结","reasons":["最多3条"],')
    lines.append(' "suggested_action":"normal|reduce|pause"}')
    lines.append("")
    lines.append("判断标准：")
    lines.append("- risk_on：趋势向上（价格在 EMA50 上方、EMA20 > EMA50），波动可控")
    lines.append("- risk_off：趋势向下或波动显著放大，建议暂停开新仓")
    lines.append("- neutral：方向不明、震荡")
    lines.append("判断时请综合：价格趋势、市场广度（涨跌家数）、衍生品（资金费率、持仓量变化、")
    lines.append("多空比、主动买卖比）、全市场市值与 BTC 占比、恐惧贪婪指数。")
    lines.append("例如：价格趋势尚可但广度极差、情绪极度恐惧、持仓量快速下降，应偏保守。")
    lines.append("- 不要预测具体价格，不要给投资建议")
    return "\n".join(lines)


def ask_deepseek(api_key, prompt):
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是严谨的市场风险状态判断器，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 500,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def parse_json(text):
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in response")
    obj = json.loads(text[start:end + 1])
    regime = str(obj.get("regime", "")).lower()
    if regime not in ("risk_on", "neutral", "risk_off"):
        raise ValueError("invalid regime: %r" % regime)
    obj["regime"] = regime
    obj["confidence"] = int(obj.get("confidence", 50))
    obj["summary"] = str(obj.get("summary", ""))[:200]
    obj["reasons"] = [str(x)[:120] for x in obj.get("reasons", [])][:3]
    action = str(obj.get("suggested_action", "")).lower()
    if action not in ("normal", "reduce", "pause"):
        action = {"risk_on": "normal", "neutral": "reduce", "risk_off": "pause"}[regime]
    obj["suggested_action"] = action
    return obj


def send_telegram(cfg, text):
    tg = cfg.get("telegram") or {}
    token, chat_id = tg.get("token"), tg.get("chat_id")
    if not token or not chat_id:
        return
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:3900]}).encode("utf-8")
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
    urllib.request.urlopen(req, timeout=30).read()


def prev_regime():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f).get("regime")
    except Exception:
        return None


def main():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    api_key = open(KEY_PATH, encoding="utf-8").read().strip()
    if not api_key.startswith("sk-"):
        raise RuntimeError("DeepSeek Key 格式不对")

    markets = [
        snapshot("BTCUSDT"), snapshot("ETHUSDT"), snapshot("ZECUSDT"),
        snapshot("SOLUSDT"), snapshot("XRPUSDT"), snapshot("DOGEUSDT"),
        snapshot("BNBUSDT"), snapshot("LINKUSDT"),
    ]
    md = market_data.collect()
    fr = (md.get("futures") or {}).get("funding_rate_pct")
    if fr is None:
        fr = funding_rate()
    prompt = build_prompt(markets, fr, md)
    log("prompt built, %d chars" % len(prompt))

    raw = ask_deepseek(api_key, prompt)
    log("deepseek raw: %s" % raw.replace("\n", " ")[:300])
    result = parse_json(raw)

    old = prev_regime()
    result.update({
        "timestamp": int(time.time()),
        "generated_at": datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "deepseek-chat",
        "market": markets,
        "market_context": md,
        "btc_funding_rate_pct": fr,
    })
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_PATH)
    log("regime written: %s (old=%s)" % (result["regime"], old))

    if result["regime"] != old:
        text = (
            "【AI 风控状态变化】\n"
            "%s -> %s（置信度 %d）\n\n%s\n\n原因：\n- %s"
            % (old or "未知", result["regime"], result["confidence"],
               result["summary"], "\n- ".join(result["reasons"]) or "无")
        )
        send_telegram(cfg, text)
        log("telegram sent (changed)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("ERROR: %r" % exc)
        raise SystemExit(1)
