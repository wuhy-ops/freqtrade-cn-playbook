"""D1a：D1 + BTC 日线 EMA200 过滤。

唯一改动：只有 BTC 收盘价在日线 EMA200 上方时才允许入场。
其余参数（20 日 Donchian 入场、3×ATR20 吊灯止损）保持不变。
"""

from typing import Optional

import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy

import talib.abstract as ta


class MyStrategyD1a(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1d"
    can_short = False

    minimal_roi = {"0": 10.0}
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
        dataframe["donchian_high"] = dataframe["high"].rolling(20).max().shift(1)

        try:
            btc = self.dp.get_pair_dataframe(pair="BTC/USDT", timeframe="1d")
            if btc is not None and not btc.empty:
                btc = btc.copy()
                btc["btc_ema200"] = ta.EMA(btc, timeperiod=200)
                btc = btc[["date", "close", "btc_ema200"]].rename(columns={"close": "btc_close"})
                dataframe = dataframe.drop(columns=["btc_close", "btc_ema200"], errors="ignore")
                dataframe = pd.merge(dataframe, btc, on="date", how="left")
        except Exception:
            pass

        # 如果 BTC 数据不可用，补空列并按不通过处理（NaN 比较为 False）
        if "btc_close" not in dataframe.columns:
            dataframe["btc_close"] = float("nan")
        if "btc_ema200" not in dataframe.columns:
            dataframe["btc_ema200"] = float("nan")
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["close"] > dataframe["donchian_high"])
            & (dataframe["btc_close"] > dataframe["btc_ema200"])
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "donchian20_btc200d"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time, current_rate: float,
                        current_profit: float, after_fill: bool = False, **kwargs) -> Optional[float]:
        try:
            if current_rate <= 0:
                return None
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            if df is None or len(df) == 0:
                return None
            atr = float(df["atr"].iloc[-1])
            if atr <= 0:
                return None
            highest = max(float(trade.max_rate or trade.open_rate or current_rate), current_rate)
            stop_price = highest - 3.0 * atr
            ratio = stop_price / current_rate - 1.0
            return max(-0.50, min(-0.02, ratio))
        except Exception:
            return None
