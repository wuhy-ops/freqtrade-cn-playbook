#!/usr/bin/env python3
"""策略诊断（方案 C）：把真实回测结果 + 策略源码交给 DeepSeek，输出改进建议。

输入：最新的 backtest-result-*.zip
输出：/opt/freqtrade/user_data/strategy_review_YYYYMMDD_HHMM.txt
     同时推送到 Telegram（分段发送）
"""

import glob
import json
import os
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

BASE = "/opt/freqtrade"
CFG_PATH = os.path.join(BASE, "user_data/config.json")
KEY_PATH = os.path.join(BASE, "deepseek.key")
RESULT_DIR = os.path.join(BASE, "user_data/backtest_results")
OUT_DIR = os.path.join(BASE, "user_data")
CST = timezone(timedelta(hours=8))


def http_json(url, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def latest_backtest(preferred="MyStrategy"):
    """取最新的回测结果；优先取包含指定策略的那份，避免误读 A/B 变体。"""
    zips = sorted(
        glob.glob(os.path.join(RESULT_DIR, "backtest-result-*.zip")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not zips:
        raise RuntimeError("没有找到回测结果 zip，请先执行 backtesting --export trades")

    fallback = None
    for path in zips:
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                json_name = [n for n in names if n.endswith(".json") and "config" not in n][0]
                py_name = [n for n in names if n.endswith(".py")][0]
                data = json.loads(zf.read(json_name).decode("utf-8"))
                strategy_src = zf.read(py_name).decode("utf-8")
        except Exception:
            continue
        if fallback is None:
            fallback = (path, data, strategy_src)
        if preferred in (data.get("strategy") or {}):
            return path, data, strategy_src

    if fallback:
        return fallback
    raise RuntimeError("没有可用的回测结果")


def fmt(x, digits=2, suffix=""):
    try:
        return ("%." + str(digits) + "f") % float(x) + suffix
    except Exception:
        return str(x)


def summarize(data):
    name = list(data["strategy"].keys())[0]
    s = data["strategy"][name]
    trades_all = [t for t in (s.get("trades") or []) if not t.get("is_open")]
    dur_by_pair = {}
    for t in trades_all:
        dur_by_pair.setdefault(t["pair"], []).append(float(t.get("trade_duration") or 0))
    lines = []

    lines.append("【总体指标】")
    lines.append("交易笔数：%s（多头 %s / 空头 %s）" % (s.get("total_trades"), s.get("trade_count_long"), s.get("trade_count_short")))
    lines.append("胜率：%s（胜 %s / 负 %s / 平 %s）" % (fmt((s.get("winrate") or 0) * 100, 2, "%"), s.get("wins"), s.get("losses"), s.get("draws")))
    lines.append("总盈亏：%s USDT（%s%%），期末余额 %s" % (
        fmt(s.get("profit_total_abs")), fmt((s.get("profit_total") or 0) * 100), fmt(s.get("final_balance"))))
    lines.append("平均每笔：%s%%，中位数 %s%%，期望值 %s" % (
        fmt((s.get("profit_mean") or 0) * 100, 3), fmt((s.get("profit_median") or 0) * 100, 3), fmt(s.get("expectancy"))))
    lines.append("最大回撤：%s USDT（%s%%），持续 %s 天" % (
        fmt(s.get("max_drawdown_abs")), fmt((s.get("max_drawdown_account") or 0) * 100), fmt((s.get("drawdown_duration_s") or 0) / 86400, 1)))
    lines.append("Sharpe %s / Sortino %s / Calmar %s / SQN %s / p 值 %s" % (
        fmt(s.get("sharpe")), fmt(s.get("sortino")), fmt(s.get("calmar")), fmt(s.get("sqn")), fmt(s.get("p_value"), 4)))
    lines.append("盈利因子：%s，CAGR：%s%%，日均交易：%s" % (
        fmt(s.get("profit_factor")), fmt((s.get("cagr") or 0) * 100), fmt(s.get("trades_per_day"), 3)))
    lines.append("同期市场涨跌：%s%%，最大连续盈利/亏损：%s / %s" % (
        fmt((s.get("market_change") or 0) * 100), s.get("max_consecutive_wins"), s.get("max_consecutive_losses")))
    lines.append("平均持仓：%s 小时；盈利单平均 %s 小时，亏损单平均 %s 小时" % (
        fmt((s.get("holding_avg_s") or 0) / 3600, 1),
        fmt((s.get("winner_holding_avg_s") or 0) / 3600, 1),
        fmt((s.get("loser_holding_avg_s") or 0) / 3600, 1)))
    lines.append("参数：timeframe=%s，stoploss=%s，trailing=%s，ROI=%s，max_open_trades=%s" % (
        s.get("timeframe"), s.get("stoploss"), s.get("trailing_stop"), s.get("minimal_roi"), s.get("max_open_trades")))

    lines.append("")
    lines.append("【分币种表现】")
    for r in s.get("results_per_pair") or []:
        if r.get("key") == "TOTAL":
            continue
        durs = dur_by_pair.get(r.get("key")) or [0]
        avg_h = sum(durs) / len(durs) / 60.0
        lines.append("%s：%s 笔，胜率 %s，盈亏 %s USDT（%s%%），平均持仓 %s 小时" % (
            r.get("key"), r.get("trades"), fmt((r.get("winrate") or 0) * 100, 1, "%"),
            fmt(r.get("profit_total_abs")), fmt((r.get("profit_total") or 0) * 100),
            fmt(avg_h, 1)))

    lines.append("")
    lines.append("【退出原因】")
    for r in s.get("exit_reason_summary") or []:
        if r.get("key") == "TOTAL":
            continue
        lines.append("%s：%s 笔，胜率 %s，合计 %s USDT，平均 %s%%" % (
            r.get("key"), r.get("trades"), fmt((r.get("winrate") or 0) * 100, 1, "%"),
            fmt(r.get("profit_total_abs")), fmt((r.get("profit_mean") or 0) * 100)))

    lines.append("")
    lines.append("【入场标签】")
    for r in s.get("results_per_enter_tag") or []:
        if r.get("key") == "TOTAL":
            continue
        lines.append("%s：%s 笔，胜率 %s，合计 %s USDT" % (
            r.get("key"), r.get("trades"), fmt((r.get("winrate") or 0) * 100, 1, "%"), fmt(r.get("profit_total_abs"))))

    pb = s.get("periodic_breakdown") or {}
    months = (pb.get("month") or [])
    if months:
        lines.append("")
        lines.append("【按月表现】")
        for m in months:
            raw = str(m.get("date"))
            if "/" in raw:
                parts = raw.split("/")
                label = "%s-%s" % (parts[2], parts[1]) if len(parts) == 3 else raw
            else:
                label = raw[:7]
            lines.append("%s：%s 笔，盈亏 %s USDT" % (
                label, m.get("trades"), fmt(m.get("profit_abs"))))

    trades = s.get("trades") or []
    closed = [t for t in trades if not t.get("is_open")]
    closed.sort(key=lambda t: t.get("profit_abs") or 0)

    def trade_line(t):
        return "%s %s -> %s，开 %.4f 平 %.4f，盈亏 %s USDT（%s%%），原因 %s，持仓 %s 小时" % (
            t.get("pair"), str(t.get("open_date"))[:16], str(t.get("close_date"))[:16],
            t.get("open_rate") or 0, t.get("close_rate") or 0,
            fmt(t.get("profit_abs")), fmt((t.get("profit_ratio") or 0) * 100),
            t.get("exit_reason"), fmt((t.get("trade_duration") or 0) / 60, 1))

    lines.append("")
    lines.append("【最差 5 笔】")
    for t in closed[:5]:
        lines.append(trade_line(t))
    lines.append("")
    lines.append("【最好 5 笔】")
    for t in closed[-5:]:
        lines.append(trade_line(t))

    # 手续费估算
    fees = 0.0
    for t in trades:
        try:
            fees += float(t.get("stake_amount") or 0) * float(t.get("fee_open") or 0)
            fees += float(t.get("amount") or 0) * float(t.get("close_rate") or 0) * float(t.get("fee_close") or 0)
        except Exception:
            pass
    lines.append("")
    lines.append("【成本】双边手续费约 %s USDT（占初始资金 %s%%），滑点未计入" % (
        fmt(fees), fmt(fees / (s.get("starting_balance") or 1000) * 100, 2)))
    return "\n".join(lines), name


def build_prompt(name, summary, strategy_src):
    prompt = []
    prompt.append("以下是我的 Freqtrade 策略源码、回测配置和完整回测统计。")
    prompt.append("请做一次严格的量化策略诊断，不要泛泛而谈，每条结论都必须引用具体数据。")
    prompt.append("")
    prompt.append("【策略源码】")
    prompt.append("```python")
    prompt.append(strategy_src[:12000])
    prompt.append("```")
    prompt.append("")
    prompt.append("【回测统计】")
    prompt.append(summary)
    prompt.append("")
    prompt.append("请按以下结构输出中文报告（纯文本，不要 markdown 表格）：")
    prompt.append("一、策略逻辑总结（3~5 行，说明它在赌什么）")
    prompt.append("二、主要问题（按严重程度排序，最多 5 条，每条必须引用上面的数字）")
    prompt.append("三、过拟合风险评估（参数数量、样本量、是否可靠）")
    prompt.append("四、具体改进方案（最多 5 条，要可落地：改哪一行逻辑、加什么过滤、如何验证）")
    prompt.append("五、结论：继续改进 / 换策略方向，并说明理由")
    prompt.append("")
    prompt.append("要求：")
    prompt.append("- 不要给具体币价预测，不要给投资建议")
    prompt.append("- 不要编造数据，只能使用上面提供的数字")
    prompt.append("- 指出问题时要说清“如果不改，实盘会发生什么”")
    prompt.append("- 改进建议要区分“低成本改动”和“需要重写”两类")
    return "\n".join(prompt)


def ask_deepseek(api_key, prompt):
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一名严格的量化策略评审，擅长用回测数据分析过拟合与逻辑缺陷。只基于给定数据，不编造。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 3000,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def send_telegram(cfg, text):
    tg = cfg.get("telegram") or {}
    token, chat_id = tg.get("token"), tg.get("chat_id")
    if not token or not chat_id:
        return
    # 按 3500 字符分段
    chunks, buf = [], ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > 3500:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf:
        chunks.append(buf)
    for i, chunk in enumerate(chunks):
        prefix = "" if i == 0 else "（续 %d/%d）\n" % (i + 1, len(chunks))
        body = urllib.parse.urlencode({"chat_id": chat_id, "text": prefix + chunk}).encode("utf-8")
        req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % token, data=body)
        urllib.request.urlopen(req, timeout=30).read()


def main():
    cfg = json.load(open(CFG_PATH, encoding="utf-8"))
    api_key = open(KEY_PATH, encoding="utf-8").read().strip()
    path, data, strategy_src = latest_backtest()
    summary, name = summarize(data)
    print("backtest:", os.path.basename(path))
    print(summary)
    print("\n=== calling deepseek ===")

    prompt = build_prompt(name, summary, strategy_src)
    report = ask_deepseek(api_key, prompt)

    stamp = datetime.now(timezone.utc).astimezone(CST).strftime("%Y%m%d_%H%M")
    out = os.path.join(OUT_DIR, "strategy_review_%s.txt" % stamp)
    header = "【策略诊断报告】%s\n策略：%s\n回测文件：%s\n生成时间：%s\n\n" % (
        stamp, name, os.path.basename(path),
        datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d %H:%M"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(header + "【回测统计摘要】\n" + summary + "\n\n" + "=" * 60 + "\n\n" + report + "\n")
    print("saved:", out)
    print("\n=== report ===\n" + report)
    send_telegram(cfg, header + report)
    print("\ntelegram sent")


if __name__ == "__main__":
    main()
