#!/usr/bin/env python3
"""B：横截面动量轮动（最后一轮）。

预注册：
  - 池子：2020-11 时点 10 币
    BTC/ETH/BNB/XRP/DOGE/ADA/LTC/LINK/ATOM/SOL
  - 每月第一个交易日再平衡
  - 信号：90 日收益率，取前 5 等权
  - B0：无 regime 过滤；B1：BTC 收盘 > 日线 EMA200 才持仓，否则空仓
  - 成本：主 0.2%/边，附 0.3%/边
  - 判定：多数年度为正 + 年度 PF 中位数 >= 1.1
          + 最大正年度利润占比 < 60%
  - 严格用再平衡日前一日的数据，next-day open 成交，无未来函数
"""

import os

import numpy as np
import pandas as pd

DATA_DIR = "/freqtrade/user_data/data/binance"
PAIRS = {
    "BTC": "BTC_USDT",
    "ETH": "ETH_USDT",
    "BNB": "BNB_USDT",
    "XRP": "XRP_USDT",
    "DOGE": "DOGE_USDT",
    "ADA": "ADA_USDT",
    "LTC": "LTC_USDT",
    "LINK": "LINK_USDT",
    "ATOM": "ATOM_USDT",
    "SOL": "SOL_USDT",
}
START = "2020-11-13"
END = "2026-11-12"
TOP_N = 5
MOM_DAYS = 90
INITIAL = 1000.0

WINDOWS = [
    ("2020-11-13", "2021-11-13"),
    ("2021-11-13", "2022-11-13"),
    ("2022-11-13", "2023-11-13"),
    ("2023-11-13", "2024-11-13"),
    ("2024-11-13", "2025-11-13"),
    ("2025-11-13", "2026-11-13"),
]


def load_data():
    closes, opens = {}, {}
    for coin, fn in PAIRS.items():
        path = os.path.join(DATA_DIR, fn + "-1d.feather")
        df = pd.read_feather(path)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        df = df.set_index("date").sort_index()
        closes[coin] = df["close"]
        opens[coin] = df["open"]
    idx = pd.date_range(START, END, freq="D", tz="UTC")
    close = pd.DataFrame(closes).reindex(idx).ffill()
    open_ = pd.DataFrame(opens).reindex(idx).ffill()
    return close, open_


def simulate(close, open_, fee, use_regime):
    idx = close.index
    mom = close / close.shift(MOM_DAYS) - 1.0
    mom_prev = mom.shift(1)
    btc_ema200 = close["BTC"].ewm(span=200, adjust=False).mean()
    btc_above = (close["BTC"] > btc_ema200).shift(1)

    reb_dates = pd.Series(idx).groupby([idx.year, idx.month]).min().tolist()
    reb_set = set(pd.Timestamp(x) for x in reb_dates)

    cash = INITIAL
    shares = {c: 0.0 for c in PAIRS}
    basis = {c: 0.0 for c in PAIRS}
    pnl = {c: 0.0 for c in PAIRS}
    fees_paid = 0.0
    turnover = 0.0
    equity = []

    for t in idx:
        if t in reb_set:
            invest = True
            if use_regime and not bool(btc_above.get(t, False)):
                invest = False

            top = []
            if invest:
                m = mom_prev.loc[t].dropna()
                m = m[[c for c in PAIRS if c in m.index]]
                if len(m) > 0:
                    top = m.sort_values(ascending=False).head(TOP_N).index.tolist()

            po = open_.loc[t]
            holdings = sum(
                shares[c] * po[c] for c in PAIRS
                if shares[c] > 0 and pd.notna(po.get(c, np.nan))
            )
            total = cash + holdings
            n = len(top)
            target = {c: total / n for c in top} if n else {}

            # 先卖后买
            for c in PAIRS:
                price = po.get(c, np.nan)
                if pd.isna(price) or price <= 0:
                    continue
                cur = shares[c] * price
                tgt = target.get(c, 0.0)
                if tgt < cur - 1e-9:
                    sell_val = cur - tgt
                    qty = sell_val / price
                    avg = basis[c] / shares[c] if shares[c] > 0 else 0.0
                    proceeds = sell_val * (1.0 - fee)
                    pnl[c] += proceeds - avg * qty
                    fees_paid += sell_val * fee
                    turnover += sell_val
                    shares[c] -= qty
                    basis[c] -= avg * qty
                    cash += proceeds

            for c in top:
                price = po.get(c, np.nan)
                if pd.isna(price) or price <= 0:
                    continue
                cur = shares[c] * price
                tgt = target[c]
                delta = tgt - cur
                if delta > 1e-9:
                    spend = min(delta, cash / (1.0 + fee))
                    if spend <= 0:
                        continue
                    qty = spend / price
                    fee_amt = spend * fee
                    shares[c] += qty
                    basis[c] += spend + fee_amt
                    cash -= spend + fee_amt
                    fees_paid += fee_amt
                    turnover += spend

        val = cash + sum(
            shares[c] * close.loc[t, c] for c in PAIRS
            if shares[c] > 0 and pd.notna(close.loc[t, c])
        )
        equity.append(val)

    eq = pd.Series(equity, index=idx, name="equity")

    # 期末未实现盈亏
    last = close.iloc[-1]
    for c in PAIRS:
        if shares[c] > 0:
            pnl[c] += shares[c] * last[c] - basis[c]

    return eq, pnl, fees_paid, turnover


def window_stats(eq):
    rows = []
    for s, e in WINDOWS:
        sub = eq[(eq.index >= pd.Timestamp(s, tz="UTC")) & (eq.index < pd.Timestamp(e, tz="UTC"))]
        if len(sub) < 2:
            rows.append({"window": s + "~" + e, "ret": None, "pf": None, "dd": None})
            continue
        ret = sub.iloc[-1] / sub.iloc[0] - 1.0
        monthly = sub.resample("ME").last().pct_change().dropna()
        gains = monthly[monthly > 0].sum()
        losses = abs(monthly[monthly < 0].sum())
        pf = (gains / losses) if losses > 0 else None
        dd = float((sub / sub.cummax() - 1.0).min())
        rows.append({"window": s + "~" + e, "ret": ret, "pf": pf, "dd": dd})
    return rows


def full_stats(eq):
    rets = eq.pct_change().dropna()
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    days = (eq.index[-1] - eq.index[0]).days
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (365.0 / days) - 1.0 if days > 0 else None
    sharpe = rets.mean() / rets.std() * np.sqrt(365) if rets.std() > 0 else None
    dd = float((eq / eq.cummax() - 1.0).min())
    return total, cagr, sharpe, dd


def evaluate(name, eq, pnl, fees, turnover):
    print("=" * 92)
    print("%s" % name)
    print("=" * 92)
    rows = window_stats(eq)
    print("%-24s %10s %8s %9s" % ("年度窗口", "收益", "PF", "回撤"))
    print("-" * 92)
    pfs = []
    positive_years = 0
    yearly = []
    for r in rows:
        ret = r["ret"]
        pf = r["pf"]
        if ret is not None:
            yearly.append(ret)
            if ret > 0:
                positive_years += 1
        if pf is not None:
            pfs.append(pf)
        print("%-24s %9s%% %8s %8s%%" % (
            r["window"],
            ("%.2f" % (ret * 100)) if ret is not None else "-",
            ("%.2f" % pf) if pf is not None else "-",
            ("%.2f" % (r["dd"] * 100)) if r["dd"] is not None else "-",
        ))

    total, cagr, sharpe, dd = full_stats(eq)
    print("-" * 92)
    print("6 年总收益：%.2f%%，CAGR：%.2f%%，Sharpe：%.2f，最大回撤：%.2f%%" % (
        total * 100, cagr * 100, sharpe or 0, dd * 100))
    print("正收益年度：%d/%d" % (positive_years, len(yearly)))

    if pfs:
        pfs_sorted = sorted(pfs)
        mid = len(pfs_sorted) // 2
        median_pf = pfs_sorted[mid] if len(pfs_sorted) % 2 else (pfs_sorted[mid - 1] + pfs_sorted[mid]) / 2
    else:
        median_pf = None

    pos_sum = sum(y for y in yearly if y > 0)
    largest = max((y for y in yearly if y > 0), default=0.0)
    share = largest / pos_sum * 100 if pos_sum > 0 else 0.0
    print("年度 PF 中位数：%s" % (("%.2f" % median_pf) if median_pf is not None else "-"))
    print("最大正年度利润占比：%.1f%%（要求 < 60%%）" % share)
    passed = (
        positive_years > len(yearly) / 2
        and median_pf is not None and median_pf >= 1.1
        and share < 60.0
        and total > 0
    )
    print("预注册判定：%s" % ("PASS" if passed else "FAIL"))
    print("交易成本合计：%.2f USDT；成交额合计：%.2f USDT" % (fees, turnover))
    print("分币种贡献（净额，含费用）：")
    for c, v in sorted(pnl.items(), key=lambda x: -x[1]):
        print("  %-6s %+9.2f" % (c, v))
    print()


def main():
    close, open_ = load_data()
    print("数据：%s ~ %s，%d 天，%d 个币" % (
        close.index[0].date(), close.index[-1].date(), len(close), len(close.columns)))

    for fee, tag in ((0.002, "0.2%/边"), (0.003, "0.3%/边")):
        for use_regime, name in ((False, "B0（无 regime）"), (True, "B1（BTC 200D）")):
            eq, pnl, fees, turnover = simulate(close, open_, fee, use_regime)
            evaluate("%s  fee=%s" % (name, tag), eq, pnl, fees, turnover)


if __name__ == "__main__":
    main()
