"""R1：均值回归（探索性）。

预注册参数，不做调参：
  - 周期：4h
  - 入场：收盘价 > EMA200（只在长期趋势向上时买回调）
          且 RSI(2) < 10（短期超卖）
  - 出场：RSI(2) > 70，或 -8% 硬止损
  - 不做空、无 ROI、无移动止盈
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from pandas import DataFrame

from freqtrade.strategy import IStrategy

import talib.abstract as ta

logger = logging.getLogger(__name__)

REGIME_FILE = "/freqtrade/user_data/ai_regime.json"
REGIME_MAX_AGE = 26 * 3600


class MyStrategyR1(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False

    minimal_roi = {"0": 10.0}  # 等于关闭 ROI
    stoploss = -0.08
    trailing_stop = False

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count = 220

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi2"] = ta.RSI(dataframe, timeperiod=2)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] > dataframe["ema200"])
            & (dataframe["rsi2"] < 10)
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "mean_reversion_rsi2"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = dataframe["rsi2"] > 70
        dataframe.loc[cond, "exit_long"] = 1
        dataframe.loc[cond, "exit_tag"] = "rsi2_recovered"
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime,
                            entry_tag: Optional[str], side: str, **kwargs) -> bool:
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
