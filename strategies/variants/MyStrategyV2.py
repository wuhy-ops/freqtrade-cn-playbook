"""V2：4 小时周期原生版本。

把整套信号重定义在 4h 上（不是把 1h 策略的 timeframe 改一下）：
  - EMA20/50/200、RSI14、成交量均值全部按 4h K 线计算
  - 24 小时成交量均值 = 6 根 4h K 线
  - 不加入 V1 的连续确认条件
  - 不加 BTC 日线过滤（保持与 E2 逻辑同源，避免混淆变量）
退出逻辑与生产策略保持一致（冻结）。
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


class MyStrategyV2(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
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

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        # 24 小时 = 6 根 4h K 线
        dataframe["vol_mean"] = dataframe["volume"].rolling(6).mean()
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
        dataframe.loc[cond, "enter_tag"] = "trend_pullback_4h_native"
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
