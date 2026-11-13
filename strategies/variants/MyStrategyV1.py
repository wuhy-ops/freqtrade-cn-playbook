"""V1：在 E3 基础上加入「连续 2 根 1h 收盘站稳 EMA20」的入场确认。

只增加一个条件，不碰退出、不碰止损、不碰 V2 的 4h 化。
信号在第二根 K 线收盘时产生，freqtrade 下一根开盘执行，无未来函数。
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

from pandas import DataFrame

from freqtrade.strategy import IStrategy, informative

import talib.abstract as ta

logger = logging.getLogger(__name__)

REGIME_FILE = "/freqtrade/user_data/ai_regime.json"
REGIME_MAX_AGE = 26 * 3600


class MyStrategyV1(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False

    minimal_roi = {"0": 0.12, "240": 0.06, "720": 0.03}
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

    @informative("1d", "BTC/USDT", fmt="{column}_{timeframe}")
    def populate_indicators_btc_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["btc_close"] = dataframe["close"]
        dataframe["btc_ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

    @informative("4h")
    def populate_indicators_4h(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        return dataframe

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
            & (dataframe["close"].shift(1) > dataframe["ema20"].shift(1))  # V1：上一根也站稳
            & (dataframe["rsi"] > 50)
            & (dataframe["rsi"] < 70)
            & (dataframe["volume"] > dataframe["vol_mean"])
            & (dataframe["btc_close_1d"] > dataframe["btc_ema200_1d"])
            & (dataframe["ema50_4h"] > dataframe["ema200_4h"])
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "trend_pullback_confirmed"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (dataframe["ema20"] < dataframe["ema50"]) | (dataframe["rsi"] > 80)
        dataframe.loc[cond, "exit_long"] = 1
        dataframe.loc[cond, "exit_tag"] = "trend_break"
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
