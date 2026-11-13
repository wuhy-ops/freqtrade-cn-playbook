"""D1：日线 Donchian 突破 + 吊灯止损（3×ATR20）。

预注册参数（不做搜索）：
  - 入场：收盘价突破前 20 日最高价
  - 出场：吊灯止损 = 持仓期间最高价 - 3 × ATR(20)
  - 只做多，现货，无 ROI、无固定止损、无 trailing
  - 标的池：BTC/ETH/SOL/DOGE/XRP（5 个全部使用，不做动态选币）
  - 不加 BTC 200 日线过滤（E1 已证明无独立 alpha）
"""

from datetime import datetime
from typing import Optional

from pandas import DataFrame

from freqtrade.strategy import IStrategy

import talib.abstract as ta


class MyStrategyD1(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = False

    # ROI 设为 1000%，等于关闭
    minimal_roi = {"0": 10.0}

    # 作为硬上限；正常由吊灯止损控制
    stoploss = -0.50
    use_custom_stoploss = True
    trailing_stop = False

    process_only_new_candles = True
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    startup_candle_count = 220

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=20)
        # 前 20 日最高价（不含当前 K 线）
        dataframe["donchian_high"] = dataframe["high"].rolling(20).max().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = dataframe["close"] > dataframe["donchian_high"]
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "donchian20_breakout"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # 不使用退出信号，只靠吊灯止损
        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time, current_rate: float,
                        current_profit: float, after_fill: bool = False, **kwargs) -> Optional[float]:
        """吊灯止损：持仓期间最高价 - 3 × ATR(20)。"""
        try:
            if current_rate <= 0:
                return None
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is None or len(df) == 0:
                return None

            atr = float(df["atr"].iloc[-1])
            if atr <= 0:
                return None

            # trade.max_rate 是 Freqtrade 维护的开仓以来最高成交价，
            # 不依赖 dataframe 子集，避免入场当天尚未进入 df 时取错最高价。
            highest = max(float(trade.max_rate or trade.open_rate or current_rate), current_rate)
            stop_price = highest - 3.0 * atr
            ratio = stop_price / current_rate - 1.0
            return max(-0.50, min(-0.02, ratio))
        except Exception:
            return None
