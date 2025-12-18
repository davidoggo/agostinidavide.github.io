# BTC Aggregated Liquidations Trading Tools  – Quantower 
This folder contains an experimental Quantower indicator pack I use to study BTC aggregated liquidations and build visualization and alerting tools.
With "BTC aggregated liquidations" I mean liquidations of coin-margined contracts + liquidations of stablecoin-margined contracts, spanning all types of contracts and then aggregated across multiple exchanges. This is achieved by connecting to Coinalyze's APIs and fetching this specific data in real-time, before saving it in a database.


## Idea
Aggregate across multiple exchanges liquidation data for BTC perpetual/future/spot contracts from Coinalyze into a local CSV file.
Map that data to the current chart timeframe in Quantower, and additionally group the current timeframe data into bins to calculate different timeframes at the same moment.

Build:
- long/short liquidation histogram
- automatic S/R levels from statistically significant liquidation spikes, in USD amount
- optional Telegram alerts (spikes + SR price touches)

Reuse the same CSV for extra sub-modules (higher timeframe visualization panel, oscillators, etc.).

The goal is to understand how to manage having both successive predictable liquidations clusters and, rarely, outliers, but also how price reacts around those levels both during short/ medium windows of time after the event.


## Current status

One large C# file with all the logic inside (fetch, CSV, S/R, HTF, oscillators, alerts).

Works in Quantower, but:

- a lot of naming choices and structures should be refined

- not yet ready as a open-source library or as a commercial product.

This as a working prototype / research sandbox.


## Refactor plan (future steps)

When I come back to this project I want to:

- Split the code into modular and more clean pieces:

  - BTC_LiqFetcher (fetch + CSV only)

  - BTC_LiqHistogram_SR (histogram + SR lines)

  - BTC_Liq_HTFPanel (higher-TF context)

  - BTC_Liq_Oscillators / BTC_Liq_EdgeLine (microstructure signals)

  - keep only what is really needed.

- Add a usage section




## Dependencies

- Quantower (for custom C# indicators / technical data).

- Coinalyze API key for fetching liquidation data.

- (Optional) Telegram bot for notifications/alerts.

Without personal keys / tokens in the code, I deleted mine and put some placeholders, no respective data will be displayed.



