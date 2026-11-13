"""M1：E3 信号 + 周度动量前 8 名过滤。

- 信号条件与 E3 相同（BTC 日线 EMA200 + 4h EMA50>EMA200 + 1h 趋势回踩）
- 额外要求：当前周该标的在 30 日动量排名前 8
- 排名由 build_momentum_ranking.py 预计算，严格使用周一之前的数据
- 标的池扩大到 20 个，通过 config_m1.json 配置
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy, informative

import talib.abstract as ta

logger = logging.getLogger(__name__)

REGIME_FILE = "/freqtrade/user_data/ai_regime.json"
RANKING_FILE = "/freqtrade/user_data/momentum_ranking.json"
REGIME_MAX_AGE = 26 * 3600

_RANKING = None


class MyStrategyM1(IStrategy):
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

    def _load_ranking(self):
        global _RANKING
        if _RANKING is None:
            with open(RANKING_FILE, encoding="utf-8") as f:
                _RANKING = json.load(f)
        return _RANKING

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["ema200"] = ta.EMA(dataframe, timeperiod=200)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["vol_mean"] = dataframe["volume"].rolling(24).mean()

        pair = metadata["pair"]
        try:
            ranking = self._load_ranking()
            allowed = {w for w, pairs in ranking.items() if pair in pairs}
            # pandas 3.x 不支持 floor("W-MON")（非固定频率），手动计算周一
            dates = dataframe["date"]
            mondays = (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.normalize()
            weeks = mondays.dt.strftime("%Y-%m-%d")
            dataframe["in_top"] = weeks.isin(allowed).astype(int)
        except Exception as exc:
            logger.warning("读取动量排名失败，默认放行: %r", exc)
            dataframe["in_top"] = 1
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        cond = (
            (dataframe["ema20"] > dataframe["ema50"])
            & (dataframe["ema50"] > dataframe["ema200"])
            & (dataframe["close"] > dataframe["ema20"])
            & (dataframe["rsi"] > 50)
            & (dataframe["rsi"] < 70)
            & (dataframe["volume"] > dataframe["vol_mean"])
            & (dataframe["btc_close_1d"] > dataframe["btc_ema200_1d"])
            & (dataframe["ema50_4h"] > dataframe["ema200_4h"])
            & (dataframe["in_top"] == 1)
        )
        dataframe.loc[cond, "enter_long"] = 1
        dataframe.loc[cond, "enter_tag"] = "trend_pullback_top8"
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
