#!/usr/bin/env python3
"""D1 系列 6 年回测报告。

预注册判定：
  - 6 个 12 个月窗口；笔数 < 8 记 inconclusive，不计入分母
  - 多数活跃窗口为正
  - 活跃窗口 PF 中位数 >= 1.1
  - 最大正窗口利润 / 所有正窗口利润之和 < 60%（利润集中度条款）
  - 总笔数 >= 40
另外打印 2021-05~2021-08 的牛市内宽幅震荡窗口，作为死因复现检查。
"""

import glob
import json
import os
import sys
import zipfile
from collections import defaultdict

RESULT_DIR = os.environ.get("FT_RESULT_DIR", "/opt/freqtrade/user_data/backtest_results")
WINDOWS = [
    "20201113-20211113",
    "20211113-20221113",
    "20221113-20231113",
    "20231113-20241113",
    "20241113-20251113",
    "20251113-20261113",
]
FULL = "20201113-"
CHOP = "20210501-20210801"
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
    print("=" * 96)
    print("%s：6 年回测（fee 0.2%%/边；结果目录 %s）" % (STRATEGY, RESULT_DIR))
    print("=" * 96)
    print("%-24s %6s %8s %11s %8s %10s" % ("窗口", "笔数", "胜率", "盈亏USDT", "PF", "判定"))
    print("-" * 96)

    total_trades = 0
    total_pnl = 0.0
    active = []
    for w in WINDOWS:
        item = best.get((STRATEGY, w))
        if not item:
            print("%-24s 缺失" % w)
            continue
        trades = closed(item[1])
        n, wr, pnl, pf = stats(trades)
        total_trades += n
        total_pnl += pnl
        verdict = "inconclusive" if n < 8 else "active"
        if n >= 8:
            active.append((w, pnl, pf))
        print("%-24s %6d %7.1f%% %11.2f %8s %10s" % (w, n, wr, pnl, fmt(pf), verdict))

    print("-" * 96)
    print("总交易数：%d，总盈亏：%+.2f USDT" % (total_trades, total_pnl))

    passed = False
    if active:
        positive = [x for x in active if x[1] > 0]
        pfs = sorted(pf for _, _, pf in active if pf is not None)
        mid = len(pfs) // 2
        median_pf = pfs[mid] if len(pfs) % 2 else (pfs[mid - 1] + pfs[mid]) / 2
        pos_sum = sum(pnl for _, pnl, _ in positive)
        largest = max((pnl for _, pnl, _ in positive), default=0.0)
        share = (largest / pos_sum * 100) if pos_sum > 0 else 0.0
        print("活跃窗口：%d 个，正收益：%d 个" % (len(active), len(positive)))
        print("活跃窗口 PF 中位数：%s" % fmt(median_pf))
        print("最大正窗口利润占比：%.1f%%（要求 < 60%%）" % share)
        passed = (
            len(positive) > len(active) / 2
            and median_pf is not None and median_pf >= 1.1
            and share < 60.0
            and total_trades >= 40
        )
        print("预注册判定：%s" % ("PASS" if passed else "FAIL"))
        if not passed:
            reasons = []
            if not (len(positive) > len(active) / 2):
                reasons.append("正收益窗口未过半")
            if median_pf is None or median_pf < 1.1:
                reasons.append("PF 中位数 < 1.1")
            if share >= 60.0:
                reasons.append("利润集中度 >= 60%")
            if total_trades < 40:
                reasons.append("总笔数 < 40")
            print("未通过原因：" + "；".join(reasons))
    else:
        print("没有足够的活跃窗口")

    # 全长与震荡子窗口
    print()
    for label, tr in (("6 年全长", FULL), ("2021 牛内震荡", CHOP)):
        item = best.get((STRATEGY, tr))
        if not item:
            print("%s %s：缺失" % (label, tr))
            continue
        trades = closed(item[1])
        n, wr, pnl, pf = stats(trades)
        print("%s %s：%d 笔，胜率 %.1f%%，盈亏 %+.2f，PF %s" % (label, tr, n, wr, pnl, fmt(pf)))

    # 分币种与退出原因（只用 6 个互不重叠窗口）
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
    print("分币种（6 个年度窗口合计）：")
    for p, (n, w, pnl) in sorted(pair_agg.items(), key=lambda x: -x[1][2]):
        print("  %-12s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (p, n, w / n * 100 if n else 0, pnl))
    print("退出原因（6 个年度窗口合计）：")
    for e, (n, w, pnl) in sorted(exit_agg.items(), key=lambda x: x[1][2]):
        print("  %-16s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (e, n, w / n * 100 if n else 0, pnl))

    # 正收益的币种集中度（诊断项，不作硬性门槛）
    pos_pairs = [(p, v[2]) for p, v in pair_agg.items() if v[2] > 0]
    if pos_pairs:
        pos_sum = sum(v for _, v in pos_pairs)
        largest = max(v for _, v in pos_pairs)
        print("最大正币种利润占比：%.1f%%" % (largest / pos_sum * 100))


if __name__ == "__main__":
    main()
