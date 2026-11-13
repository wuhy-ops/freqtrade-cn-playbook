#!/usr/bin/env python3
"""滚动窗口 + 分币种 + 退出原因汇总。

读取 backtest_results 中每个 (策略, 时间段) 的最新结果，
按 5 个滚动窗口输出收益/PF，并跨窗口汇总分币种与退出原因。
"""

import glob
import json
import os
import sys
import zipfile
from collections import defaultdict
from datetime import datetime

RESULT_DIR = "/opt/freqtrade/user_data/backtest_results"
WINDOWS = [
    "20240801-20250201",
    "20250201-20250801",
    "20250801-20260201",
    "20260201-20260801",
    "20260801-20261113",
]
STRATS = sys.argv[1:] if len(sys.argv) > 1 else ["MyStrategyE2", "MyStrategyE3", "MyStrategyV1", "MyStrategyV2"]


def load_latest():
    best = {}
    for path in glob.glob(os.path.join(RESULT_DIR, "backtest-result-*.zip")):
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                jn = [n for n in names if n.endswith(".json") and "config" not in n][0]
                data = json.loads(zf.read(jn).decode("utf-8"))
        except Exception:
            continue
        mtime = os.path.getmtime(path)
        for name, s in (data.get("strategy") or {}).items():
            tr = s.get("timerange")
            if isinstance(tr, dict):
                tr = tr.get("timerange") or ""
            key = (name, str(tr))
            if key not in best or mtime > best[key][0]:
                best[key] = (mtime, s, path)
    return best


def agg(trades):
    closed = [t for t in trades if not t.get("is_open")]
    n = len(closed)
    if not n:
        return n, 0.0, 0.0, None
    wins = sum(1 for t in closed if (t.get("profit_abs") or 0) > 0)
    pnl = sum((t.get("profit_abs") or 0) for t in closed)
    gw = sum((t.get("profit_abs") or 0) for t in closed if (t.get("profit_abs") or 0) > 0)
    gl = abs(sum((t.get("profit_abs") or 0) for t in closed if (t.get("profit_abs") or 0) < 0))
    pf = (gw / gl) if gl else None
    return n, wins / n * 100.0, pnl, pf


def repeat_stats(trades):
    """同币上一笔平仓后 72 小时内再次入场的比例。"""
    by_pair = defaultdict(list)
    for t in trades:
        if t.get("is_open"):
            continue
        by_pair[t.get("pair")].append(t)
    reentries = 0
    within = 0
    for _, ts in by_pair.items():
        ts.sort(key=lambda x: str(x.get("open_date") or ""))
        for prev, cur in zip(ts, ts[1:]):
            try:
                prev_close = datetime.fromisoformat(str(prev.get("close_date")))
                cur_open = datetime.fromisoformat(str(cur.get("open_date")))
            except Exception:
                continue
            reentries += 1
            if (cur_open - prev_close).total_seconds() <= 72 * 3600:
                within += 1
    return reentries, within


def fmt(x, d=2, suffix=""):
    return ("%." + str(d) + "f") % x + suffix if x is not None else "-"


def main():
    best = load_latest()
    for strat in STRATS:
        print("=" * 78)
        print("策略:", strat)
        print("-" * 78)
        print("滚动窗口:")
        total_n = 0
        total_pnl = 0.0
        positive = 0
        pfs = []
        for w in WINDOWS:
            item = best.get((strat, w))
            if not item:
                print("  %-20s  无结果" % w)
                continue
            s = item[1]
            n, wr, pnl, pf = agg(s.get("trades") or [])
            total_n += n
            total_pnl += pnl
            if pnl > 0:
                positive += 1
            if pf is not None:
                pfs.append(pf)
            print("  %-20s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f  PF %s  回撤 %s%%" % (
                w, n, wr, pnl, fmt(pf), fmt((s.get("max_drawdown_account") or 0) * 100)))
        pfs_sorted = sorted(pfs)
        median_pf = None
        if pfs_sorted:
            mid = len(pfs_sorted) // 2
            median_pf = pfs_sorted[mid] if len(pfs_sorted) % 2 else (pfs_sorted[mid - 1] + pfs_sorted[mid]) / 2
        print("  合计: %d 笔，盈亏 %+.2f，正收益窗口 %d/%d，PF 中位数 %s" % (
            total_n, total_pnl, positive, len(WINDOWS), fmt(median_pf)))

        pair_agg = defaultdict(lambda: [0, 0, 0.0])
        exit_agg = defaultdict(lambda: [0, 0, 0.0])
        total_reentries = 0
        total_within_72 = 0
        for w in WINDOWS:
            item = best.get((strat, w))
            if not item:
                continue
            wtrades = [t for t in (item[1].get("trades") or []) if not t.get("is_open")]
            rn, wn = repeat_stats(wtrades)
            total_reentries += rn
            total_within_72 += wn
            for t in wtrades:
                pnl = t.get("profit_abs") or 0.0
                p = t.get("pair")
                e = t.get("exit_reason") or "unknown"
                for d, k in ((pair_agg, p), (exit_agg, e)):
                    d[k][0] += 1
                    d[k][1] += 1 if pnl > 0 else 0
                    d[k][2] += pnl
        print("分币种（跨窗口合计）:")
        for p, (n, w, pnl) in sorted(pair_agg.items(), key=lambda x: -x[1][2]):
            print("  %-12s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (p, n, w / n * 100 if n else 0, pnl))
        print("退出原因（跨窗口合计）:")
        for e, (n, w, pnl) in sorted(exit_agg.items(), key=lambda x: x[1][2]):
            print("  %-14s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (e, n, w / n * 100 if n else 0, pnl))
        rate = total_within_72 / total_reentries * 100 if total_reentries else 0.0
        print("重复入场（同币平仓后 72h 内再入场）：%d/%d = %.1f%%" % (
            total_within_72, total_reentries, rate))
        print()


if __name__ == "__main__":
    main()
