#!/usr/bin/env python3
"""生成周度动量排名，供 M1 策略使用。

规则：
  - 每周一 00:00 UTC，用「上一日收盘」计算 30 日收益率
  - 在 20 个标的池中取前 8 名
  - 结果写入 momentum_ranking.json：{ "YYYY-MM-DD": ["BTC/USDT", ...] }
  - 严格使用周一之前的数据，避免未来函数
"""

import json
import os

import pandas as pd

DATA_DIR = os.environ.get("FT_DATA", "/freqtrade/user_data/data/binance")
OUT_PATH = os.environ.get("FT_OUT", "/freqtrade/user_data/momentum_ranking.json")

BASES = [
    "BTC", "ETH", "ZEC", "SOL", "XRP", "PEPE", "NEAR", "UNI", "BNB", "DOGE",
    "LINK", "AVAX", "TRX", "LTC", "INJ", "ATOM", "DOT", "BCH", "ADA", "APT",
]
TOP_N = 8
START = "2023-11-13"


def main():
    closes = {}
    for base in BASES:
        path = os.path.join(DATA_DIR, "%s_USDT-1d.feather" % base)
        if not os.path.exists(path):
            print("missing:", path)
            continue
        df = pd.read_feather(path)
        df = df[["date", "close"]].dropna().sort_values("date")
        series = df.set_index("date")["close"]
        closes["%s/USDT" % base] = series

    close = pd.DataFrame(closes).sort_index()
    ret = close / close.shift(30) - 1.0
    print("pairs loaded:", len(close.columns), "rows:", len(close))

    mondays = pd.date_range(
        start=pd.Timestamp(START, tz="UTC"),
        end=close.index.max(),
        freq="W-MON",
    )

    schedule = {}
    for m in mondays:
        idx = ret.index[ret.index < m]
        if len(idx) == 0:
            continue
        row = ret.loc[idx[-1]].dropna()
        if row.empty:
            continue
        top = list(row.sort_values(ascending=False).index[:TOP_N])
        schedule[m.strftime("%Y-%m-%d")] = top

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=1)

    keys = sorted(schedule)
    print("weeks:", len(keys))
    for k in keys[:3] + keys[-3:]:
        print(k, schedule[k])


if __name__ == "__main__":
    main()
