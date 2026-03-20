from __future__ import annotations
import json, math, os, time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests

@dataclass(frozen=True)
class Settings:
    # Market data
    symbol: str = os.getenv("SYMBOL", "BTCUSDT")
    category: str = os.getenv("CATEGORY", "spot")          # spot | linear | inverse
    interval: str = os.getenv("INTERVAL", "1h")            # 1m,3m,5m,15m,30m,1h,2h,4h,6h,12h,1d,1w,1M
    bars: int = int(os.getenv("BARS", "1500"))
    request_timeout: int = int(os.getenv("REQUEST_TIMEOUT", "20"))

    # EMA skeleton signal
    ema_fast: int = int(os.getenv("EMA_FAST", "20"))
    ema_slow: int = int(os.getenv("EMA_SLOW", "50"))

    # Abs DD z-score zone
    z_lookback: int = int(os.getenv("Z_LOOKBACK", "500"))
    z_k1: float = float(os.getenv("Z_K1", "0.5"))
    z_k2: float = float(os.getenv("Z_K2", "1.0"))

    # Dashboard
    dashboard_bars: int = int(os.getenv("DASHBOARD_BARS", "220"))
    save_dashboard: bool = os.getenv("SAVE_DASHBOARD", "1") == "1"
    show_dashboard: bool = os.getenv("SHOW_DASHBOARD", "1") == "1"
    dashboard_path: str = os.getenv("DASHBOARD_PATH", "btc_absdd_dashboard.png")

    # Telegram
    telegram_enabled: bool = os.getenv("TELEGRAM_ENABLED", "0") == "1"
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_no_signal_message: bool = os.getenv("TELEGRAM_NO_SIGNAL_MESSAGE", "1") == "1"

    # State
    state_path: str = os.getenv("STATE_PATH", "monitor_state.json")

    # Execution
    exec_mode: str = os.getenv("EXEC_MODE", "Close")          # Close | Next Open
    ignore_neutral: bool = os.getenv("IGNORE_NEUTRAL", "1") == "1"

    # Analytics basis
    pnl_mode: str = os.getenv("PNL_MODE", "Raw")              # Raw | VolAdj
    vol_metric: str = os.getenv("VOL_METRIC", "ATR%")         # ATR% | StDevReturns%
    vol_period: int = int(os.getenv("VOL_PERIOD", "14"))
    vol_floor_pct: float = float(os.getenv("VOL_FLOOR_PCT", "0.0001"))

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"

def interval_to_ms(interval: str) -> int:
    mapping = {
        "1": 60_000,
        "3": 3 * 60_000,
        "5": 5 * 60_000,
        "15": 15 * 60_000,
        "30": 30 * 60_000,
        "60": 60 * 60_000,
        "120": 120 * 60_000,
        "240": 240 * 60_000,
        "360": 360 * 60_000,
        "720": 720 * 60_000,
        "D": 24 * 60 * 60_000,
        "W": 7 * 24 * 60 * 60_000,
        "M": 30 * 24 * 60 * 60_000,
    }
    if interval not in mapping:
        raise ValueError(f"Unsupported interval: {interval}")
    return mapping[interval]


def fetch_bybit_klines(symbol: str, category: str, interval: str, bars: int, timeout: int) -> pd.DataFrame:
    session = requests.Session()
    all_rows = []
    end: Optional[int] = None

    while len(all_rows) < bars:
        limit = min(1000, bars - len(all_rows))
        params = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        if end is not None:
            params["end"] = end

        r = session.get(BYBIT_KLINE_URL, params=params, timeout=timeout)
        r.raise_for_status()
        payload = r.json()

        if payload.get("retCode") != 0:
            raise RuntimeError(f"Bybit error: {payload}")

        rows = payload["result"]["list"]
        if not rows:
            break

        all_rows.extend(rows)

        # Bybit returns reverse chronological order
        oldest_start = int(rows[-1][0])
        end = oldest_start - 1

        if len(rows) < limit:
            break

        time.sleep(0.12)

    if not all_rows:
        raise RuntimeError("No kline data returned from Bybit.")

    df = pd.DataFrame(
        all_rows,
        columns=[
            "start_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        ],
    )

    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["start_time"] = pd.to_datetime(pd.to_numeric(df["start_time"]), unit="ms", utc=True)
    df = df.sort_values("start_time").drop_duplicates("start_time").reset_index(drop=True)

    candle_ms = interval_to_ms(interval)
    now_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    start_ms = (df["start_time"].astype("int64") // 10**6).astype(np.int64)
    df = df[(start_ms + candle_ms) <= now_ms].copy().reset_index(drop=True)

    if df.empty:
        raise RuntimeError("All candles appear unclosed after filtering.")

    return df[["start_time", "open", "high", "low", "close", "volume"]]

def build_signal_ema(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = df.copy()

    out["ema_fast"] = out["close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=slow, adjust=False).mean()

    out["signal"] = np.where(
        out["ema_fast"] > out["ema_slow"],
        1,
        np.where(out["ema_fast"] < out["ema_slow"], -1, 0),
    ).astype(int)

    prev_signal = out["signal"].shift(1)
    out["signal_change"] = out["signal"].ne(prev_signal).fillna(True)

    # Regime crossover markers for the top chart
    out["long_cross"] = out["signal_change"] & (out["signal"] == 1) & prev_signal.notna()
    out["short_cross"] = out["signal_change"] & (out["signal"] == -1) & prev_signal.notna()

    return out

def compute_pnl_analytics(
    df: pd.DataFrame,
    z_lookback: int,
    z_k1: float,
    z_k2: float,
    exec_mode: str = "Close",
    ignore_neutral: bool = True,
    pnl_mode: str = "Raw",
    vol_metric: str = "ATR%",
    vol_period: int = 14,
    vol_floor_pct: float = 0.0001,
) -> pd.DataFrame:
    """
    TradingView-style PnL / Abs DD core with:
    - Close / Next Open execution
    - long / short / flat regime handling
    - side-based segment PnL
    - Abs DD from segment peak
    - optional volatility-adjusted analytics basis
    - z-score on Abs DD basis used for classification
    """
    if exec_mode not in {"Close", "Next Open"}:
        raise ValueError("exec_mode must be 'Close' or 'Next Open'")
    if pnl_mode not in {"Raw", "VolAdj"}:
        raise ValueError("pnl_mode must be 'Raw' or 'VolAdj'")
    if vol_metric not in {"ATR%", "StDevReturns%"}:
        raise ValueError("vol_metric must be 'ATR%' or 'StDevReturns%'")

    out = df.copy().reset_index(drop=True)
    n = len(out)

    entry = np.full(n, np.nan, dtype=float)
    direction = np.zeros(n, dtype=int)
    pending_dir = np.zeros(n, dtype=int)
    pending_bar = np.full(n, np.nan, dtype=float)
    entry_bar = np.full(n, np.nan, dtype=float)

    pnl_pct = np.zeros(n, dtype=float)
    peak_pnl = np.zeros(n, dtype=float)
    abs_dd = np.zeros(n, dtype=float)

    cur_entry, cur_dir, cur_pending_dir = np.nan, 0, 0
    cur_pending_bar, cur_entry_bar = np.nan, np.nan
    cur_pnl, cur_peak, cur_abs_dd = 0.0, 0.0, 0.0

    for i in range(n):
        sig = int(out.at[i, "signal"])
        sig_change = bool(out.at[i, "signal_change"])
        close_i = float(out.at[i, "close"])
        open_i = float(out.at[i, "open"])

        waiting_next_open = (
            exec_mode == "Next Open"
            and cur_pending_dir != 0
            and not math.isnan(cur_pending_bar)
            and math.isnan(cur_entry)
        )

        if sig_change or (math.isnan(cur_entry) and not waiting_next_open):
            if ignore_neutral and sig == 0:
                cur_dir, cur_pending_dir = 0, 0
                cur_entry, cur_pending_bar, cur_entry_bar = np.nan, np.nan, np.nan
                cur_pnl, cur_peak, cur_abs_dd = 0.0, 0.0, 0.0
            elif exec_mode == "Close":
                cur_dir, cur_pending_dir = sig, 0
                cur_entry, cur_entry_bar = close_i, float(i)
                cur_pending_bar = np.nan
                cur_pnl, cur_peak, cur_abs_dd = 0.0, 0.0, 0.0
            else:
                cur_dir, cur_entry, cur_entry_bar = 0, np.nan, np.nan
                cur_pending_dir, cur_pending_bar = sig, float(i)
                cur_pnl, cur_peak, cur_abs_dd = 0.0, 0.0, 0.0

        if exec_mode == "Next Open" and cur_pending_dir != 0 and not math.isnan(cur_pending_bar) and i > cur_pending_bar:
            cur_dir, cur_entry, cur_entry_bar = cur_pending_dir, open_i, float(i)
            cur_pending_dir, cur_pending_bar = 0, np.nan
            cur_pnl, cur_peak, cur_abs_dd = 0.0, 0.0, 0.0

        if cur_dir != 0 and not math.isnan(cur_entry) and cur_entry != 0.0:
            cur_pnl = 100.0 * cur_dir * (close_i - cur_entry) / cur_entry
            cur_peak = max(cur_peak, cur_pnl)
            cur_abs_dd = cur_peak - cur_pnl
        else:
            cur_pnl = 0.0
            cur_peak = 0.0
            cur_abs_dd = 0.0

        entry[i], direction[i], pending_dir[i] = cur_entry, cur_dir, cur_pending_dir
        pending_bar[i], entry_bar[i] = cur_pending_bar, cur_entry_bar
        pnl_pct[i], peak_pnl[i], abs_dd[i] = cur_pnl, cur_peak, cur_abs_dd

    out["entry"] = entry
    out["dir"] = direction
    out["pending_dir"] = pending_dir
    out["pending_bar"] = pending_bar
    out["entry_bar"] = entry_bar
    out["pnl_pct"] = pnl_pct
    out["peak_pnl"] = peak_pnl
    out["abs_dd"] = abs_dd

    out["active"] = (
        (out["dir"] != 0)
        & out["entry"].notna()
        & (np.arange(n) != out["entry_bar"].fillna(-999999).to_numpy())
    )

    out["side"] = np.where(out["dir"] == 1, "LONG", np.where(out["dir"] == -1, "SHORT", "FLAT"))

    close_s, high_s, low_s = out["close"].astype(float), out["high"].astype(float), out["low"].astype(float)
    prev_close = close_s.shift(1)

    tr = pd.concat(
        [
            high_s - low_s,
            (high_s - prev_close).abs(),
            (low_s - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(vol_period, min_periods=vol_period).mean()
    atr_pct = (atr / close_s.replace(0.0, np.nan)) * 100.0

    ret_log = np.log(close_s / close_s.shift(1))
    ret_std_pct = ret_log.rolling(vol_period, min_periods=vol_period).std(ddof=0) * 100.0

    vol_den = atr_pct if vol_metric == "ATR%" else ret_std_pct
    out["vol_den"] = vol_den.clip(lower=vol_floor_pct)
    if pnl_mode == "VolAdj":
        out["pnl_view"] = out["pnl_pct"] / out["vol_den"]
        out["abs_dd_view"] = out["abs_dd"] / out["vol_den"]
    else:
        out["pnl_view"] = out["pnl_pct"]
        out["abs_dd_view"] = out["abs_dd"]

    # Pine-style masked stats on the chosen analytics basis
    abs_dd_basis_vals, active_vals = out["abs_dd_view"].to_numpy(dtype=float), out["active"].to_numpy(dtype=bool)

    abs_dd_mean, abs_dd_std, abs_dd_z = np.full(n, np.nan, dtype=float), np.full(n, np.nan, dtype=float), np.full(n, np.nan, dtype=float)

    for i in range(n):
        lo = max(0, i - z_lookback + 1)
        win_x = abs_dd_basis_vals[lo:i + 1]
        win_mask = active_vals[lo:i + 1]

        xs = win_x[win_mask & np.isfinite(win_x)]

        if xs.size > 0:
            m = xs.mean()
            sd = xs.std(ddof=0)
            abs_dd_mean[i], abs_dd_std[i] = m, sd
            if sd != 0.0 and np.isfinite(abs_dd_basis_vals[i]):
                abs_dd_z[i] = (abs_dd_basis_vals[i] - m) / sd

    out["abs_dd_mean"] = abs_dd_mean
    out["abs_dd_std"] = abs_dd_std
    out["abs_dd_z"] = abs_dd_z

    out["in_zone"] = (
        out["active"]
        & out["abs_dd_z"].ge(z_k1)
        & out["abs_dd_z"].lt(z_k2)
    )

    return out

def load_state(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
    except Exception:
        return {}


def save_state(path: str, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")

def render_dashboard(df: pd.DataFrame, cfg: Settings) -> str:
    tail = df.tail(cfg.dashboard_bars).copy()
    latest = tail.iloc[-1]

    price_color = "#099881" if latest["dir"] == 1 else "#F33644" if latest["dir"] == -1 else "#888888"
    zone_color = price_color
    is_raw = cfg.pnl_mode == "Raw"
    pnl_label = "PnL %" if is_raw else f"PnL / {cfg.vol_metric}"
    absdd_label = "Abs DD %" if is_raw else f"Abs DD / {cfg.vol_metric}"
    basis_label = "Raw" if is_raw else f"VolAdj ({cfg.vol_metric})"

    fig = plt.figure(figsize=(15, 10), layout="constrained")
    gs = fig.add_gridspec(4, 1, height_ratios=[3.0, 1.5, 1.5, 1.6], hspace=0.10)

    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[1, 0], sharex=ax1)
    ax3 = fig.add_subplot(gs[2, 0], sharex=ax1)
    ax4 = fig.add_subplot(gs[3, 0], sharex=ax1)

    # -------------------------
    # Top panel: BTC + EMAs + cross markers
    # -------------------------
    ax1.plot(tail["start_time"], tail["close"], linewidth=1.2, label="Close")
    ax1.plot(tail["start_time"], tail["ema_fast"], linewidth=1.3, label=f"EMA {cfg.ema_fast}")
    ax1.plot(tail["start_time"], tail["ema_slow"], linewidth=1.3, label=f"EMA {cfg.ema_slow}")

    long_mask = tail["long_cross"].fillna(False)
    short_mask = tail["short_cross"].fillna(False)

    ax1.scatter(
        tail.loc[long_mask, "start_time"],
        tail.loc[long_mask, "close"],
        marker="x",
        s=48,
        linewidths=1.4,
        color="#099881",
        label="Long cross",
        zorder=5,
    )
    ax1.scatter(
        tail.loc[short_mask, "start_time"],
        tail.loc[short_mask, "close"],
        marker="x",
        s=48,
        linewidths=1.4,
        color="#F33644",
        label="Short cross",
        zorder=5,
    )

    ax1.set_ylabel("BTC Price")
    ax1.grid(alpha=0.25)
    ax1.legend(loc="upper left")

    # -------------------------
    # PnL panel: segmented by side
    # -------------------------
    pnl_long = tail["pnl_view"].where(tail["dir"] == 1)
    pnl_short = tail["pnl_view"].where(tail["dir"] == -1)
    pnl_flat = tail["pnl_view"].where(tail["dir"] == 0)

    ax2.plot(tail["start_time"], pnl_long, color="#099881", linewidth=1.8, label="PnL LONG")
    ax2.plot(tail["start_time"], pnl_short, color="#F33644", linewidth=1.8, label="PnL SHORT")
    ax2.plot(tail["start_time"], pnl_flat, color="#888888", linewidth=1.0, alpha=0.8, label="PnL FLAT")
    ax2.axhline(0.0, color="gray", linewidth=0.9, alpha=0.6)
    ax2.set_ylabel(pnl_label)
    ax2.grid(alpha=0.25)
    ax2.legend(loc="upper left")

    # -------------------------
    # Abs DD panel
    # -------------------------
    ax3.plot(tail["start_time"], tail["abs_dd_view"], color="#F33644", linewidth=1.4, label=absdd_label)
    ax3.set_ylabel(absdd_label)
    ax3.grid(alpha=0.25)
    ax3.legend(loc="upper left")

    # -------------------------
    # Z-score panel
    # -------------------------
    ax4.plot(tail["start_time"], tail["abs_dd_z"], linewidth=1.5, label="Abs DD Z-score")
    ax4.axhline(cfg.z_k1, color="orange", linestyle="--", linewidth=1.0, label=f"K1={cfg.z_k1}")
    ax4.axhline(cfg.z_k2, color="red", linestyle="--", linewidth=1.0, label=f"K2={cfg.z_k2}")
    ax4.fill_between(
        tail["start_time"],
        cfg.z_k1,
        cfg.z_k2,
        alpha=0.08,
        color=zone_color,
        label="Alert zone",
    )
    ax4.set_ylabel("Z")
    ax4.grid(alpha=0.25)
    ax4.legend(loc="upper left")

    # Highlight latest bar if it is in zone
    if bool(latest["in_zone"]) and len(tail) >= 2:
        for ax in [ax1, ax2, ax3, ax4]:
            ax.axvspan(
                tail["start_time"].iloc[-2],
                tail["start_time"].iloc[-1],
                alpha=0.10,
                color=zone_color,
            )

    z_str = f"{latest['abs_dd_z']:.3f}" if pd.notna(latest["abs_dd_z"]) else "nan"
    pnl_str, dd_str = (f"{latest['pnl_view']:.3f}" if pd.notna(latest["pnl_view"]) else "nan", f"{latest['abs_dd_view']:.3f}" if pd.notna(latest["abs_dd_view"]) else "nan")

    summary = (
        f"{cfg.symbol} | {cfg.category} | interval={cfg.interval} | "
        f"side={latest['side']} | basis={basis_label} | "
        f"close={latest['close']:.2f} | pnl={pnl_str} | absdd={dd_str} | z={z_str}"
    )
    fig.suptitle(summary, fontsize=11, y=1.01)
    fig.savefig(cfg.dashboard_path, dpi=160, bbox_inches="tight")

    if cfg.show_dashboard:
        plt.show()
    else:
        plt.close(fig)

    return cfg.dashboard_path

def send_telegram_photo(bot_token: str, chat_id: str, image_path: str, caption: str, timeout: int) -> None:
    if not bot_token or not chat_id:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    with open(image_path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{bot_token}/sendPhoto", 
                         data={"chat_id": chat_id, "caption": caption[:1024]}, files={"photo": f}, timeout=timeout)
        r.raise_for_status()
        if not r.json().get("ok", False):
            raise RuntimeError(f"Telegram error: {r.json()}")

def send_telegram_message(bot_token: str, chat_id: str, text: str, timeout: int) -> None:
    if not bot_token or not chat_id:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    r = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text[:4096],
        },
        timeout=timeout,
    )
    r.raise_for_status()

    payload = r.json()
    if not payload.get("ok", False):
        raise RuntimeError(f"Telegram error: {payload}")

def build_status_caption(row: pd.Series, cfg: Settings, triggered: bool) -> str:
    basis_label = "Raw" if cfg.pnl_mode == "Raw" else f"VolAdj ({cfg.vol_metric})"
    status = "Yes signal" if triggered else "No signal"

    pnl_str = f"{row['pnl_view']:.3f}" if pd.notna(row["pnl_view"]) else "nan"
    dd_str = f"{row['abs_dd_view']:.3f}" if pd.notna(row["abs_dd_view"]) else "nan"
    z_str = f"{row['abs_dd_z']:.3f}" if pd.notna(row["abs_dd_z"]) else "nan"

    return (
        f"{cfg.symbol} | {cfg.interval}\n"
        f"{status}\n"
        f"Side: {row['side']}\n"
        f"Basis: {basis_label}\n"
        f"Close: {row['close']:.2f}\n"
        f"PnL: {pnl_str}\n"
        f"Abs DD: {dd_str}\n"
        f"Abs DD Z: {z_str}\n"
        f"Zone: [{cfg.z_k1}, {cfg.z_k2})"
    )


def should_send_alert(df: pd.DataFrame, state: dict) -> bool:
    if len(df) < 2:
        return False

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    latest_ts = str(latest["start_time"])
    already_sent_for_bar = state.get("last_alert_bar") == latest_ts

    entered_zone_now = bool(latest["in_zone"]) and not bool(previous["in_zone"])
    return entered_zone_now and not already_sent_for_bar


def main() -> None:
    cfg = Settings()

    df = fetch_bybit_klines(
        symbol=cfg.symbol,
        category=cfg.category,
        interval=cfg.interval,
        bars=cfg.bars,
        timeout=cfg.request_timeout,
    )

    df = build_signal_ema(df, fast=cfg.ema_fast, slow=cfg.ema_slow)
    df = compute_pnl_analytics(
        df,
        z_lookback=cfg.z_lookback,
        z_k1=cfg.z_k1,
        z_k2=cfg.z_k2,
        exec_mode=cfg.exec_mode,
        ignore_neutral=cfg.ignore_neutral,
        pnl_mode=cfg.pnl_mode,
        vol_metric=cfg.vol_metric,
        vol_period=cfg.vol_period,
        vol_floor_pct=cfg.vol_floor_pct,
    )

    min_needed = max(cfg.ema_slow, cfg.z_lookback, cfg.vol_period) + 5
    if len(df) < min_needed:
        raise RuntimeError(f"Not enough data after indicator warmup. Need at least {min_needed} closed bars.")

    latest = df.iloc[-1]
    state = load_state(cfg.state_path)

    print("\n=== BTC ABS DD MONITOR ===")
    print(f"symbol      : {cfg.symbol}")
    print(f"category    : {cfg.category}")
    print(f"interval    : {cfg.interval}")
    print(f"last candle : {latest['start_time']}")
    print(f"signal      : {latest['side']}")
    print(f"basis       : {'Raw' if cfg.pnl_mode == 'Raw' else f'VolAdj ({cfg.vol_metric})'}")
    print(f"close       : {latest['close']:.2f}")
    print(f"pnl raw     : {latest['pnl_pct']:.4f}")
    print(f"abs dd raw  : {latest['abs_dd']:.4f}")
    print(f"pnl view    : {latest['pnl_view']:.4f}" if pd.notna(latest["pnl_view"]) else "pnl view    : nan")
    print(f"abs dd view : {latest['abs_dd_view']:.4f}" if pd.notna(latest["abs_dd_view"]) else "abs dd view : nan")
    print(f"abs dd z    : {latest['abs_dd_z']:.4f}" if pd.notna(latest["abs_dd_z"]) else "abs dd z    : nan")
    print(f"in zone     : {bool(latest['in_zone'])}")
    print("==========================\n")

    triggered = should_send_alert(df, state)

    image_path = cfg.dashboard_path
    need_dashboard = cfg.save_dashboard or cfg.show_dashboard or cfg.telegram_enabled

    if need_dashboard:
        image_path = render_dashboard(df, cfg)
        print(f"Dashboard saved to: {image_path}")

    if cfg.telegram_enabled:
        caption = build_status_caption(latest, cfg, triggered)
        send_telegram_photo(
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            image_path=image_path,
            caption=caption,
            timeout=cfg.request_timeout,
        )

        if triggered:
            print("Telegram image sent with YES SIGNAL caption.")
            state["last_alert_bar"] = str(latest["start_time"])
        else:
            print("Telegram image sent with NO SIGNAL caption.")

    state["last_bar"] = str(latest["start_time"])
    state["last_signal"] = int(latest["dir"])
    state["last_in_zone"] = bool(latest["in_zone"])
    save_state(cfg.state_path, state)

    return


if __name__ == "__main__":
    main()
