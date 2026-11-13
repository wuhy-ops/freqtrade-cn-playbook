#!/usr/bin/env python3
"""D1 日线 Donchian 回测报告。

预注册判定：
  - 笔数 < 8 的窗口记为 inconclusive，不计入分母
  - 活跃窗口中多数为正，且 PF 中位数 >= 1.1 -> 可进入前向验证
  - 总笔数 < 40 -> 整轮标记为 exploratory，只做定性判断
"""

import glob
import json
import os
import sys
import zipfile
from collections import defaultdict

RESULT_DIR = "/opt/freqtrade/user_data/backtest_results"
WINDOWS = [
    "20231113-20240513",
    "20240513-20241113",
    "20241113-20250513",
    "20250513-20251113",
    "20251113-20260513",
    "20260513-20261113",
]
TRAIN = "20231113-20251113"
OOS = "20251113-"
STRATEGY = sys.argv[1] if len(sys.argv) > 1 else "MyStrategyD1"


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


def closed(s):
    return [t for t in (s.get("trades") or []) if not t.get("is_open")]


def stats(trades):
    n = len(trades)
    if n == 0:
        return 0, 0.0, 0.0, None
    wins = sum(1 for t in trades if (t.get("profit_abs") or 0) > 0)
    pnl = sum((t.get("profit_abs") or 0) for t in trades)
    gw = sum((t.get("profit_abs") or 0) for t in trades if (t.get("profit_abs") or 0) > 0)
    gl = abs(sum((t.get("profit_abs") or 0) for t in trades if (t.get("profit_abs") or 0) < 0))
    pf = (gw / gl) if gl else None
    return n, wins / n * 100.0, pnl, pf


def fmt(x, d=2):
    return ("%." + str(d) + "f") % x if x is not None else "-"


def main():
    best = load_latest()
    rows = []
    print("=" * 88)
    print("%s：日线 Donchian20 + 吊灯止损 3×ATR20（fee 0.2%%/边）" % STRATEGY)
    print("=" * 88)
    print("%-22s %6s %8s %10s %8s %8s" % ("窗口", "笔数", "胜率", "盈亏USDT", "PF", "判定"))
    print("-" * 88)

    total_trades = 0
    total_pnl = 0.0
    active = []
    for w in WINDOWS:
        item = best.get((STRATEGY, w))
        if not item:
            print("%-22s 缺失" % w)
            continue
        trades = closed(item[1])
        n, wr, pnl, pf = stats(trades)
        total_trades += n
        total_pnl += pnl
        if n < 8:
            verdict = "inconclusive"
        else:
            verdict = "active"
            active.append((w, pnl, pf))
        print("%-22s %6d %7.1f%% %10.2f %8s %8s" % (w, n, wr, pnl, fmt(pf), verdict))

    print("-" * 88)
    print("总交易数：%d，总盈亏：%+.2f USDT" % (total_trades, total_pnl))
    if total_trades < 40:
        print("样本量判定：EXPLORATORY（总笔数 < 40，只能做定性判断）")

    if active:
        positive = sum(1 for _, pnl, _ in active if pnl > 0)
        pfs = sorted(pf for _, _, pf in active if pf is not None)
        mid = len(pfs) // 2
        median_pf = pfs[mid] if len(pfs) % 2 else (pfs[mid - 1] + pfs[mid]) / 2
        print("活跃窗口：%d 个，正收益：%d 个" % (len(active), positive))
        print("活跃窗口 PF 中位数：%s" % fmt(median_pf))
        passed = positive > len(active) / 2 and median_pf is not None and median_pf >= 1.1
        print("预注册判定：%s" % ("PASS（可进入前向验证）" if passed and total_trades >= 40 else "FAIL / 需继续观察"))
    else:
        print("无足够活跃窗口")

    # 训练/样本外
    print()
    for label, tr in (("训练段", TRAIN), ("样本外", OOS)):
        item = best.get((STRATEGY, tr))
        if not item:
            continue
        trades = closed(item[1])
        n, wr, pnl, pf = stats(trades)
        print("%s %s：%d 笔，胜率 %.1f%%，盈亏 %+.2f，PF %s" % (label, tr, n, wr, pnl, fmt(pf)))

    # 分币种与退出原因
    # 只用 6 个互不重叠的窗口汇总，避免和训练/样本外重复计数
    all_trades = []
    for w in WINDOWS:
        item = best.get((STRATEGY, w))
        if item:
            all_trades.extend(closed(item[1]))

    pair_agg = defaultdict(lambda: [0, 0, 0.0])
    exit_agg = defaultdict(lambda: [0, 0, 0.0])
    for t in all_trades:
        p = t.get("pair")
        e = t.get("exit_reason") or "unknown"
        pnl = t.get("profit_abs") or 0.0
        for d, k in ((pair_agg, p), (exit_agg, e)):
            d[k][0] += 1
            d[k][1] += 1 if pnl > 0 else 0
            d[k][2] += pnl

    print()
    print("分币种（所有区间合计）：")
    for p, (n, w, pnl) in sorted(pair_agg.items(), key=lambda x: -x[1][2]):
        print("  %-12s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (p, n, w / n * 100 if n else 0, pnl))
    print("退出原因（所有区间合计）：")
    for e, (n, w, pnl) in sorted(exit_agg.items(), key=lambda x: x[1][2]):
        print("  %-16s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (e, n, w / n * 100 if n else 0, pnl))


if __name__ == "__main__":
    main()
