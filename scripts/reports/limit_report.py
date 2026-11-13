#!/usr/bin/env python3
"""回踩限价实验（L1）报告。

重点三件事（按用户指定顺序）：
  1. 成交率 = L1 成交笔数 / E3 信号笔数（E3 同期交易数近似）
  2. 平均每笔期望 %（L1 vs E3）
  3. ROI 退出占比（L1 vs E3）

另附窗口收益、PF、分币种、退出原因。
"""

import glob
import json
import os
import zipfile
from collections import defaultdict

RESULT_DIR = "/opt/freqtrade/user_data/backtest_results"
WINDOWS = [
    "20240801-20250201",
    "20250201-20250801",
    "20250801-20260201",
    "20260201-20260801",
    "20260801-20261113",
]
EXTRA = ["20240801-20251113", "20251113-"]


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


def avg_pct(trades):
    if not trades:
        return 0.0
    return sum((t.get("profit_ratio") or 0) for t in trades) / len(trades) * 100.0


def pf(trades):
    gw = sum((t.get("profit_abs") or 0) for t in trades if (t.get("profit_abs") or 0) > 0)
    gl = abs(sum((t.get("profit_abs") or 0) for t in trades if (t.get("profit_abs") or 0) < 0))
    return gw / gl if gl else None


def roi_share(trades):
    if not trades:
        return 0.0
    n = sum(1 for t in trades if t.get("exit_reason") == "roi")
    return n / len(trades) * 100.0


def main():
    best = load_latest()
    print("=" * 96)
    print("L1 回踩限价 vs E3 市价（fee 0.2%/边）")
    print("成交率定义为：L1 成交笔数 / 同窗口 E3 交易笔数（信号数近似）")
    print("=" * 96)
    print("%-20s %7s %7s %8s %9s %9s %8s %8s" % (
        "窗口", "E3笔", "L1笔", "成交率", "L1期望%", "E3期望%", "L1 PF", "ROI占比"))
    print("-" * 96)

    agg_l1 = []
    agg_e3 = []
    for w in WINDOWS + EXTRA:
        e3 = best.get(("MyStrategyE3", w))
        l1 = best.get(("MyStrategyL1", w))
        if not e3 or not l1:
            print("%-20s  缺失" % w)
            continue
        e3t = closed(e3[1])
        l1t = closed(l1[1])
        fill = len(l1t) / len(e3t) * 100 if e3t else 0.0
        print("%-20s %7d %7d %7.1f%% %8.2f%% %8.2f%% %8s %7.1f%%" % (
            w, len(e3t), len(l1t), fill, avg_pct(l1t), avg_pct(e3t),
            ("%.2f" % pf(l1t)) if pf(l1t) is not None else "-", roi_share(l1t)))
        if w in WINDOWS:
            agg_l1.extend(l1t)
            agg_e3.extend(e3t)

    print("-" * 96)
    fill_all = len(agg_l1) / len(agg_e3) * 100 if agg_e3 else 0.0
    print("滚动窗口合计：E3 %d 笔，L1 %d 笔，成交率 %.1f%%" % (len(agg_e3), len(agg_l1), fill_all))
    print("L1 平均每笔 %+.3f%%，E3 平均每笔 %+.3f%%" % (avg_pct(agg_l1), avg_pct(agg_e3)))
    print("L1 总盈亏 %+.2f USDT，PF %s；ROI 占比 %.1f%%" % (
        sum((t.get("profit_abs") or 0) for t in agg_l1),
        ("%.2f" % pf(agg_l1)) if pf(agg_l1) is not None else "-",
        roi_share(agg_l1)))

    pair_agg = defaultdict(lambda: [0, 0, 0.0])
    exit_agg = defaultdict(lambda: [0, 0, 0.0])
    for t in agg_l1:
        p = t.get("pair")
        e = t.get("exit_reason") or "unknown"
        pnl = t.get("profit_abs") or 0.0
        for d, k in ((pair_agg, p), (exit_agg, e)):
            d[k][0] += 1
            d[k][1] += 1 if pnl > 0 else 0
            d[k][2] += pnl

    print()
    print("L1 分币种（滚动窗口合计）：")
    for p, (n, w, pnl) in sorted(pair_agg.items(), key=lambda x: -x[1][2]):
        print("  %-12s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (p, n, w / n * 100 if n else 0, pnl))
    print("L1 退出原因（滚动窗口合计）：")
    for e, (n, w, pnl) in sorted(exit_agg.items(), key=lambda x: x[1][2]):
        print("  %-14s %4d 笔  胜率 %5.1f%%  盈亏 %+8.2f" % (e, n, w / n * 100 if n else 0, pnl))


if __name__ == "__main__":
    main()
