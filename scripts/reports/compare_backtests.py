#!/usr/bin/env python3
"""汇总 backtest_results 里各策略的回测结果，输出对比表。

只统计 MyStrategy / MyStrategyB1..B4，并按 (策略, 时间段) 保留最新一份。
"""

import glob
import json
import os
import zipfile

RESULT_DIR = "/opt/freqtrade/user_data/backtest_results"
WANTED = {
    "MyStrategy", "MyStrategyB1", "MyStrategyB2", "MyStrategyB3", "MyStrategyB4",
    "MyStrategyE1", "MyStrategyE2", "MyStrategyE3",
    "MyStrategyV1", "MyStrategyV2",
}


def load_zip(path):
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        jn = [n for n in names if n.endswith(".json") and "config" not in n][0]
        return json.loads(zf.read(jn).decode("utf-8"))


def main():
    rows = []
    for path in glob.glob(os.path.join(RESULT_DIR, "backtest-result-*.zip")):
        try:
            data = load_zip(path)
        except Exception:
            continue
        mtime = os.path.getmtime(path)
        for name, s in (data.get("strategy") or {}).items():
            if name not in WANTED:
                continue
            tr = s.get("timerange")
            if isinstance(tr, dict):
                tr = tr.get("timerange") or ""
            rows.append({
                "strategy": name,
                "timerange": str(tr),
                "trades": s.get("total_trades"),
                "winrate": (s.get("winrate") or 0) * 100,
                "profit_pct": (s.get("profit_total") or 0) * 100,
                "profit_abs": s.get("profit_total_abs") or 0,
                "drawdown": (s.get("max_drawdown_account") or 0) * 100,
                "profit_factor": s.get("profit_factor"),
                "expectancy": s.get("expectancy"),
                "hold_h": (s.get("holding_avg_s") or 0) / 3600,
                "mtime": mtime,
                "_path": path,
            })

    # 每个 (策略, 时间段) 只保留最新一次
    best = {}
    for r in rows:
        key = (r["strategy"], r["timerange"])
        if key not in best or r["mtime"] > best[key]["mtime"]:
            best[key] = r

    rows = sorted(best.values(), key=lambda r: (r["timerange"], r["strategy"]))
    if not rows:
        print("没有找到匹配的回测结果。")
        return

    header = "%-14s %-20s %6s %7s %9s %10s %9s %7s %8s %7s" % (
        "策略", "时间段", "笔数", "胜率", "收益%", "收益USDT", "回撤%", "PF", "期望值", "持仓h")
    print(header)
    print("-" * len(header))
    for r in rows:
        print("%-14s %-20s %6s %6.1f%% %8.2f%% %10.2f %8.2f%% %7s %8s %7.1f" % (
            r["strategy"], r["timerange"], r["trades"], r["winrate"],
            r["profit_pct"], r["profit_abs"], r["drawdown"],
            ("%.2f" % r["profit_factor"]) if r["profit_factor"] is not None else "-",
            ("%.2f" % r["expectancy"]) if r["expectancy"] is not None else "-",
            r["hold_h"],
        ))


if __name__ == "__main__":
    main()
