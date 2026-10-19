# SPDX-License-Identifier: GPL-3.0-or-later
# Uses Freqtrade's documented IStrategy interface; research baseline, not an alpha claim.
from datetime import datetime
from pandas import DataFrame
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from technical import qtpylib


class PerpBaseline15m(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    startup_candle_count = 200
    process_only_new_candles = True
    minimal_roi = {}
    stoploss = -0.03
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    order_types = {"entry": "market", "exit": "market", "stoploss": "market",
                   "stoploss_on_exchange": False}
    plot_config = {"main_plot": {"ema_fast": {}, "ema_slow": {}},
                   "subplots": {"RSI": {"rsi": {}}}}

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=12)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=48)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        active = dataframe["volume"] > 0
        dataframe.loc[active & qtpylib.crossed_above(dataframe["ema_fast"], dataframe["ema_slow"])
                      & (dataframe["rsi"] < 75), ["enter_long", "enter_tag"]] = (1, "ema_cross_long")
        dataframe.loc[active & qtpylib.crossed_below(dataframe["ema_fast"], dataframe["ema_slow"])
                      & (dataframe["rsi"] > 25), ["enter_short", "enter_tag"]] = (1, "ema_cross_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[qtpylib.crossed_below(dataframe["ema_fast"], dataframe["ema_slow"]), "exit_long"] = 1
        dataframe.loc[qtpylib.crossed_above(dataframe["ema_fast"], dataframe["ema_slow"]), "exit_short"] = 1
        return dataframe
