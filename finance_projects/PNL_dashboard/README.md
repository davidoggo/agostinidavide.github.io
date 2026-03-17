BTC Abslute Drawdown Monitor

This is a simple Python project that monitors BTCUSDT market data from Bybit, builds an EMA-based long/short signal, calculates segment PnL, absolute drawdown, and the z-score of absolute drawdown, and sends a Telegram alert when the drawdown z-score enters a chosen zone.
The main goal of this project is to study regime-based PnL behavior and detect moments where the absolute drawdown is statistically between two thresholds.

What the script does
* downloads historical BTCUSDT candles from Bybit
* builds 2 EMAs
* creates long and short regimes from EMA crossover
* calculates PnL for each segment
* calculates peak PnL and absolute drawdown
* computes a rolling z-score on the absolute drawdown
* checks if the z-score is between K1 and K2
* builds a dashboard image
* optionally sends a Telegram alert with the image
* can run locally or automatically with GitHub Actions

How the logic works
1. If EMA fast is above EMA slow, the signal is long.
2. If EMA fast is below EMA slow, the signal is short.
3. Each time the signal changes, a new PnL segment starts.
4. In long mode, PnL moves like price.
5. In short mode, PnL moves opposite to price.
6. For each segment, the script tracks the best PnL reached so far.
7. Absolute drawdown is:
       abs_dd = peak_pnl - current_pnl
8. Then the script computes a rolling z-score on absolute drawdown.
9. If the z-score is in the interval:
       K1 <= z < K2      then the bar is considered inside the alert zone.
Important note
The PnL line color shows the regime:
* green = long regime
* red = short regime
* gray = flat

Files
* btc_absdd_monitor.py
  main Python script
* requirements.txt
  Python dependencies
* .github/workflows/monitor.yml
  GitHub Actions workflow to run the monitor automatically


Main settings
The script uses environment variables.

Market data
* SYMBOL
* CATEGORY
* INTERVAL
* BARS
* REQUEST_TIMEOUT

EMA signal
* EMA_FAST
* EMA_SLOW

Z-score zone
* Z_LOOKBACK
* Z_K1
* Z_K2

Dashboard
* DASHBOARD_BARS
* SAVE_DASHBOARD
* SHOW_DASHBOARD
* DASHBOARD_PATH

Execution
* EXEC_MODE
* IGNORE_NEUTRAL

Analytics basis
* PNL_MODE
* VOL_METRIC
* VOL_PERIOD
* VOL_FLOOR_PCT

Telegram
* TELEGRAM_ENABLED
* TELEGRAM_BOT_TOKEN
* TELEGRAM_CHAT_ID

State
* STATE_PATH

Raw vs VolAdj
The script can work in 2 modes.
1. Raw
   The z-score is computed directly from raw absolute drawdown.
2. VolAdj
   The script normalizes PnL and absolute drawdown by volatility before computing the z-score.

Available volatility metrics:
* ATR%
* StDevReturns%
This means the statistical classification can be done either on raw values or on volatility-adjusted values.

Dashboard
The dashboard contains 4 panels:
1. BTC price with EMA fast and EMA slow
2. PnL panel with long and short segments
3. Absolute drawdown panel
4. Absolute drawdown z-score panel with K1 and K2 thresholds

The chart also shows:
* green x at long crossovers
* red x at short crossovers
* highlighted last bar if it is in the alert zone

How to run locally
1. Install Python
2. Install dependencies      pip install -r requirements.txt
3. Run the script            python btc_absdd_monitor.py

Example PowerShell settings
$env:SHOW_DASHBOARD="1"
$env:SAVE_DASHBOARD="1"
$env:PNL_MODE="Raw"
python btc_absdd_monitor.py

Example with volatility adjustment
$env:PNL_MODE="VolAdj"
$env:VOL_METRIC="ATR%"
python btc_absdd_monitor.py

Telegram alerts
If Telegram is enabled, the script sends an alert only when the latest bar enters the zone and the previous bar was outside the zone.
This avoids repeated alerts on the same bar.

This project can be scheduled with GitHub Actions without keeping locally the computer on.

Why I made this project
I wanted a simple but solid project to study:
* regime-based PnL
* drawdown behavior
* z-score based filtering
* alert automation
* Python scripting for market analytics

It is also a good base for future improvements, for example:
* replacing the EMA signal with a more advanced strategy
* porting my TradingView Pine logic into Python
* adding more assets
* saving history to a database
* improving the dashboard
* deploying to cloud services

Current limitations
* the signal is still a simple EMA crossover
* the statistics depend on the selected lookback window
* different volatility normalization choices change the z-score behavior



This is a student project built to learn how to combine:
* market data
* signal generation
* PnL analytics
* drawdown statistics
* Telegram alerts
* GitHub Actions automation
