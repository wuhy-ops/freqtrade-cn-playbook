"""V3c：4h + 固定 2×ATR 止损（修正版）。

修正 V3/V3b 的实现错误：
  - custom_stoploss 返回的是相对 current_rate 的距离，
    原实现 -2×ATR/current_rate 实际是移动止损；
  - self.stoploss=-0.08 会把自定义止损截断在 8%。
本版改为：
  - 开仓时记录 ATR，止损价固定为 open_rate - 2×ATR；
  - 按 current_rate 换算成比例返回，使绝对止损价固定；
  - stoploss = -0.20 作为硬上限；
  - trailing_stop = False。
信号、入场、ROI、趋势退出保持不变。
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


class MyStrategyV3c(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "4h"
    can_short = False

    minimal_roi = {"0": 0.12, "240": 0.06, "720": 0.03}

    # 硬上限放宽到 -20%，否则 2×ATR 会被 -8% 截断
    stoploss = -0.20

    use_custom_stoploss = True
    trailing_stop = False

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
        dataframe.loc[cond, "enter_tag"] = "trend_pullback_4h_atr_fixed"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (dataframe["ema20"] < dataframe["ema50"]) | (dataframe["rsi"] > 80)
        dataframe.loc[cond, "exit_long"] = 1
        dataframe.loc[cond, "exit_tag"] = "trend_break"
        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time, current_rate: float,
                        current_profit: float, after_fill: bool = False, **kwargs) -> Optional[float]:
        try:
            if current_rate <= 0:
                return None

            entry_atr = trade.get_custom_data("atr_entry")
            if entry_atr is None:
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if df is None or len(df) == 0:
                    return None
                entry_atr = float(df["atr"].iloc[-1])
                if entry_atr <= 0:
                    return None
                trade.set_custom_data("atr_entry", entry_atr)
            else:
                entry_atr = float(entry_atr)

            # 固定止损价：开仓价 - 2 × 开仓时 ATR
            stop_price = trade.open_rate - 2.0 * entry_atr
            ratio = stop_price / current_rate - 1.0
            return max(-0.20, min(-0.02, ratio))
        except Exception:
            return None

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
