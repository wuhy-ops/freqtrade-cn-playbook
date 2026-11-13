import json
import logging
import time
from datetime import datetime
from typing import Optional

from pandas import DataFrame

from freqtrade.strategy import IStrategy

import talib.abstract as ta

logger = logging.getLogger(__name__)

# AI 风控状态文件（由 ai_regime.py 每天生成一次）
REGIME_FILE = "/freqtrade/user_data/ai_regime.json"
REGIME_MAX_AGE = 26 * 3600  # 超过 26 小时的旧状态自动失效（降级为放行）


class MyStrategy(IStrategy):
    """示例策略：多头趋势中回踩买入。

    注意：这只是教学示例，回测好看不代表实盘能赚钱。
    先用 dry-run 观察，再逐步改造成自己的逻辑。

    实盘/模拟盘会读取 AI 风控状态：
      regime = risk_off -> 否决开新仓
      其他情况或状态文件缺失/过期 -> 放行（fail-open）
    回测时忽略该状态，保证历史结果可复现。
    """

    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False

    # 止盈：时间越久要求越低
    minimal_roi = {
        "0": 0.12,
        "240": 0.06,
        "720": 0.03,
    }

    # 硬止损 8%
    stoploss = -0.08

    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.06
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count = 210

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["vol_mean"] = dataframe["volume"].rolling(24).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["ema50"] > dataframe["ema200"])
            & (dataframe["close"] > dataframe["ema20"])
            & (dataframe["rsi"] > 50)
            & (dataframe["rsi"] < 70)
            & (dataframe["volume"] > dataframe["vol_mean"])
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "trend_pullback"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (dataframe["ema20"] < dataframe["ema50"]) | (dataframe["rsi"] > 80)
        dataframe.loc[cond, "exit_long"] = 1
        dataframe.loc[cond, "exit_tag"] = "trend_break"
        return dataframe

    # ------------------------------------------------------------------
    # AI 风控：开仓前检查每日市场状态
    # ------------------------------------------------------------------
    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime,
                            entry_tag: Optional[str], side: str, **kwargs) -> bool:
        # 回测时忽略 AI 状态文件，保证历史结果可复现
        try:
            if self.dp.runmode.value not in ("live", "dry_run"):
                return True
        except Exception:
            pass

        allowed, regime, reason = self._ai_regime_allows_entry()
        if not allowed:
            logger.info("AI 风控否决开仓: pair=%s regime=%s reason=%s", pair, regime, reason)
        return allowed

    def _ai_regime_allows_entry(self):
        try:
            with open(REGIME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return True, "unknown", "状态文件不存在，默认放行"

        ts = float(data.get("timestamp", 0))
        if time.time() - ts > REGIME_MAX_AGE:
            return True, str(data.get("regime", "unknown")), "状态文件过期，默认放行"

        regime = str(data.get("regime", "neutral")).lower()
        summary = str(data.get("summary", ""))[:120]
        if regime == "risk_off":
            return False, regime, summary
        return True, regime, summary
