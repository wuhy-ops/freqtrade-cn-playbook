#!/usr/bin/env python3
"""公开市场数据采集（无 API Key）。

来源：
  - 币安现货：全部 USDT 交易对的 24h 行情（广度、涨跌幅榜）
  - 币安永续：资金费率、持仓量及 24h 变化、大户多空比、全市场多空比、主动买卖比
  - CoinGecko：全市场总市值、24h 变化、BTC/ETH/稳定币占比、成交量变化
  - Alternative.me：恐惧贪婪指数

所有数据源都做了容错，单个失败不影响其他数据，也不会中断调用方。
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

SNAPSHOT_PATH = "/opt/freqtrade/user_data/market_snapshot.json"
FRED_KEY_PATH = "/opt/freqtrade/fred.key"
CST = timezone(timedelta(hours=8))

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "AEUR", "USD1"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

# 市场广度样本的最小 24h 成交额（USDT）
BREADTH_MIN_QUOTE_VOLUME = 1_000_000


def _get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "freqtrade-market-data/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_all_tickers():
    return _get_json("https://api.binance.com/api/v3/ticker/24hr", timeout=25)


def breadth(tickers):
    rows = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in STABLES:
            continue
        if base.endswith(LEVERAGED_SUFFIXES) and len(base) > 3:
            continue
        try:
            qv = float(t.get("quoteVolume") or 0)
            chg = float(t.get("priceChangePercent") or 0)
            last = float(t.get("lastPrice") or 0)
        except Exception:
            continue
        if qv < BREADTH_MIN_QUOTE_VOLUME or last <= 0:
            continue
        rows.append({"symbol": base + "/USDT", "change": chg, "quote_volume": qv, "price": last})

    rows.sort(key=lambda x: x["quote_volume"], reverse=True)
    top = rows[:100]
    up = sum(1 for r in top if r["change"] > 0)
    down = sum(1 for r in top if r["change"] < 0)
    # 涨跌榜只在成交额前 100 中排名，避免低流动性小币的极端波动刷屏
    by_change = sorted(top, key=lambda x: x["change"], reverse=True)
    gainers = [{"symbol": r["symbol"], "change": round(r["change"], 2)} for r in by_change[:5]]
    losers = [{"symbol": r["symbol"], "change": round(r["change"], 2)} for r in by_change[-5:]]
    return {
        "universe": len(rows),
        "top100_up": up,
        "top100_down": down,
        "top100_flat": len(top) - up - down,
        "gainers": gainers,
        "losers": list(reversed(losers)),
    }


def futures_metrics(symbol="BTCUSDT"):
    out = {}
    try:
        p = _get_json("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=%s" % symbol)
        out["funding_rate_pct"] = round(float(p.get("lastFundingRate", 0)) * 100, 5)
    except Exception as exc:
        out["funding_error"] = str(exc)

    try:
        hist = _get_json(
            "https://fapi.binance.com/futures/data/openInterestHist?symbol=%s&period=1h&limit=25" % symbol
        )
        if hist:
            first = float(hist[0]["sumOpenInterest"])
            last = float(hist[-1]["sumOpenInterest"])
            out["open_interest"] = round(last, 2)
            out["open_interest_value_usd"] = round(float(hist[-1]["sumOpenInterestValue"]), 0)
            out["open_interest_change_24h_pct"] = round((last / first - 1) * 100, 2) if first else None
    except Exception as exc:
        out["open_interest_error"] = str(exc)

    try:
        d = _get_json(
            "https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol=%s&period=1h&limit=1" % symbol
        )
        if d:
            out["top_trader_long_short_ratio"] = round(float(d[-1]["longShortRatio"]), 3)
            out["top_trader_long_pct"] = round(float(d[-1]["longAccount"]) * 100, 1)
    except Exception as exc:
        out["top_trader_error"] = str(exc)

    try:
        d = _get_json(
            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=%s&period=1h&limit=1" % symbol
        )
        if d:
            out["global_long_short_ratio"] = round(float(d[-1]["longShortRatio"]), 3)
    except Exception as exc:
        out["global_long_short_error"] = str(exc)

    try:
        d = _get_json(
            "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol=%s&period=1h&limit=1" % symbol
        )
        if d:
            out["taker_buy_sell_ratio"] = round(float(d[-1]["buySellRatio"]), 3)
    except Exception as exc:
        out["taker_error"] = str(exc)
    return out


def coingecko_global():
    d = _get_json("https://api.coingecko.com/api/v3/global")["data"]
    pct = d.get("market_cap_percentage") or {}
    return {
        "total_market_cap_usd_t": round(float(d["total_market_cap"]["usd"]) / 1e12, 3),
        "market_cap_change_24h_pct": round(float(d.get("market_cap_change_percentage_24h_usd") or 0), 2),
        "btc_dominance_pct": round(float(pct.get("btc") or 0), 2),
        "eth_dominance_pct": round(float(pct.get("eth") or 0), 2),
        "stablecoin_dominance_pct": round(float(pct.get("usdt") or 0) + float(pct.get("usdc") or 0), 2),
        "volume_change_24h_pct": round(float(d.get("volume_change_percentage_24h_usd") or 0), 2),
    }


def fear_greed():
    d = _get_json("https://api.alternative.me/fng/?limit=1")["data"][0]
    return {"value": int(d["value"]), "classification": d["value_classification"]}


def _fred_series(series_id, api_key, limit=8):
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        "?series_id=%s&api_key=%s&file_type=json&sort_order=desc&limit=%d"
        % (series_id, api_key, limit)
    )
    d = _get_json(url, timeout=20)
    obs = [o for o in d.get("observations", []) if o.get("value") not in (".", None, "")]
    if not obs:
        raise RuntimeError("empty series %s" % series_id)
    return obs


def fred_macro():
    """美国宏观数据：10Y 国债收益率、美元指数、纳斯达克综合指数。"""
    try:
        api_key = open(FRED_KEY_PATH, encoding="utf-8").read().strip()
    except Exception:
        return {"error": "未配置 FRED API Key（%s）" % FRED_KEY_PATH}
    if not api_key:
        return {"error": "FRED API Key 为空"}

    out = {}

    def one(series_id, label, pct):
        obs = _fred_series(series_id, api_key)
        latest = float(obs[0]["value"])
        prev = float(obs[1]["value"]) if len(obs) > 1 else latest
        change = (latest / prev - 1) * 100 if pct else latest - prev
        out[label] = {"value": latest, "change": change, "as_of": obs[0]["date"]}

    try:
        one("DGS10", "us10y", pct=False)
    except Exception as exc:
        out["us10y_error"] = str(exc)
    try:
        one("DTWEXBGS", "dxy", pct=True)
    except Exception as exc:
        out["dxy_error"] = str(exc)
    try:
        one("NASDAQCOM", "nasdaq", pct=True)
    except Exception as exc:
        out["nasdaq_error"] = str(exc)
    return out


def collect(save=True):
    result = {"collected_at": datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M:%S")}

    try:
        tickers = binance_all_tickers()
        result["breadth"] = breadth(tickers)
    except Exception as exc:
        result["breadth"] = {"error": str(exc)}

    try:
        result["futures"] = futures_metrics("BTCUSDT")
    except Exception as exc:
        result["futures"] = {"error": str(exc)}

    try:
        result["global"] = coingecko_global()
    except Exception as exc:
        result["global"] = {"error": str(exc)}

    try:
        result["fear_greed"] = fear_greed()
    except Exception as exc:
        result["fear_greed"] = {"error": str(exc)}

    try:
        result["macro"] = fred_macro()
    except Exception as exc:
        result["macro"] = {"error": str(exc)}

    if save:
        try:
            tmp = SNAPSHOT_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            os.replace(tmp, SNAPSHOT_PATH)
        except Exception:
            pass
    return result


def format_for_prompt(md):
    """把采集结果格式化成提示词中的几段文本。"""
    md = md or {}
    lines = []

    b = md.get("breadth") or {}
    lines.append("【市场广度（币安 USDT 交易对，成交额前 100）】")
    if b and "error" not in b:
        lines.append("上涨 %s 个 / 下跌 %s 个 / 平盘 %s 个（有效样本 %s 个）" % (
            b.get("top100_up"), b.get("top100_down"), b.get("top100_flat"), b.get("universe")))
        g = b.get("gainers") or []
        l = b.get("losers") or []
        if g:
            lines.append("24h 领涨：" + "、".join("%s %+.2f%%" % (x["symbol"], x["change"]) for x in g))
        if l:
            lines.append("24h 领跌：" + "、".join("%s %+.2f%%" % (x["symbol"], x["change"]) for x in l))
    else:
        lines.append("获取失败：" + str(b.get("error", "unknown")))

    f = md.get("futures") or {}
    lines.append("")
    lines.append("【BTC 永续合约】")
    if f and "error" not in f:
        if "funding_rate_pct" in f:
            lines.append("资金费率：%+.5f%%（正值为多头付给空头）" % f["funding_rate_pct"])
        if "open_interest" in f:
            lines.append("持仓量：%s BTC（约 %s 亿美元），24h 变化 %+.2f%%" % (
                f["open_interest"], round((f.get("open_interest_value_usd") or 0) / 1e8, 1),
                f.get("open_interest_change_24h_pct") or 0))
        if "top_trader_long_short_ratio" in f:
            lines.append("大户账户多空比：%s（多头占比 %s%%）" % (
                f["top_trader_long_short_ratio"], f.get("top_trader_long_pct")))
        if "global_long_short_ratio" in f:
            lines.append("全市场账户多空比：%s" % f["global_long_short_ratio"])
        if "taker_buy_sell_ratio" in f:
            lines.append("近 1h 主动买卖比：%s" % f["taker_buy_sell_ratio"])
    else:
        lines.append("获取失败：" + str(f.get("error", "unknown")))

    g = md.get("global") or {}
    lines.append("")
    lines.append("【全市场】")
    if g and "error" not in g:
        lines.append("总市值：%s 万亿美元（24h %+.2f%%），成交量 24h %+.2f%%" % (
            g.get("total_market_cap_usd_t"), g.get("market_cap_change_24h_pct"), g.get("volume_change_24h_pct")))
        lines.append("BTC 占比 %.2f%%，ETH 占比 %.2f%%，稳定币（USDT+USDC）占比 %.2f%%" % (
            g.get("btc_dominance_pct"), g.get("eth_dominance_pct"), g.get("stablecoin_dominance_pct")))
    else:
        lines.append("获取失败：" + str(g.get("error", "unknown")))

    fg = md.get("fear_greed") or {}
    lines.append("")
    lines.append("【市场情绪】")
    if fg and "error" not in fg:
        lines.append("恐惧贪婪指数：%s（%s）" % (fg.get("value"), fg.get("classification")))
    else:
        lines.append("获取失败：" + str(fg.get("error", "unknown")))

    m = md.get("macro") or {}
    lines.append("")
    lines.append("【宏观（FRED，美国）】")
    if m and "error" not in m:
        if "us10y" in m:
            lines.append("10 年期国债收益率：%.2f%%（较前值 %+.2f 个百分点，数据日 %s）" % (
                m["us10y"]["value"], m["us10y"]["change"], m["us10y"]["as_of"]))
        if "dxy" in m:
            lines.append("美元指数（广义）：%.2f（较前值 %+.2f%%，数据日 %s）" % (
                m["dxy"]["value"], m["dxy"]["change"], m["dxy"]["as_of"]))
        if "nasdaq" in m:
            lines.append("纳斯达克综合指数：%.0f（较前值 %+.2f%%，数据日 %s）" % (
                m["nasdaq"]["value"], m["nasdaq"]["change"], m["nasdaq"]["as_of"]))
        errs = [v for k, v in m.items() if k.endswith("_error")]
        if errs:
            lines.append("部分数据获取失败：" + "；".join(errs))
    else:
        lines.append(m.get("error") or "获取失败")
    return "\n".join(lines)
