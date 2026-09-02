import pandas as pd
import numpy as np

# 1. Configuration & Parameters
# HISTORICAL_DATA_FILE = "mes_1sec_RTH_20260406_to_20260410.csv"
HISTORICAL_DATA_FILE = "mes_1sec_RTH_20260202_to_20260313.csv"

ES_MULTIPLIER = 50.0

# --- RSI PARAMETERS ---
RSI_TIMEFRAME = "1min"  # Resample 1-sec data to this timeframe before computing RSI
# Examples: "1min", "5min", "15min", "30min", "1h"
RSI_LENGTH = 3  # RSI period
RSI_OVERSOLD = 10  # RSI <= this → Buy signal
RSI_OVERBOUGHT = 90  # RSI >= this → Sell signal

# --- SLIPPAGE ---
TICK_SIZE = 0.25  # MES/ES tick size in points
SLIPPAGE_TICKS = 1  # Ticks of slippage applied to every fill (entry, ladder, TP, SL)

# --- MULTI-RUNG LADDER PARAMETERS ---
# Add as many rungs as you like: [x1, x2, x3, ...]
LADDER_RUNGS = [5]  # Points against initial entry to add 1 contract
Y_STOP_LOSS_POINTS = 10  # Stop loss from AVERAGE price
TP_POINTS = 7  # Take profit from AVERAGE price

# --- COOLDOWN ---
COOLDOWN_MINUTES = 0  # Minimum minutes to wait after a trade exits before taking the next signal
# Set to 0 to disable. Useful on trend days where RSI stays extreme.

# --- TRADING HOURS (US/Eastern) ---
START_TRADING_TIME = "10:00"  # No entries before this time
END_TRADING_TIME = "15:00"  # No entries at or after this time

# --- GUARDRAIL FILTER ---
STOP_TRADING_DAILY = True  # Stop trading if daily PnL targets hit
STOP_TRADING_ABOVE = 50000.0  # Daily Profit Cap
STOP_TRADING_BELOW = -1000.0  # Daily Loss Floor

FLIP_TRADE_DIRECTIONS = False  # Flip Buy/Sell logic for testing
REVERSE_ON_SIGNAL = False  # Close current trade and reverse when an opposite RSI signal fires


def compute_rsi(series: pd.Series, length: int) -> pd.Series:
    """Wilder's RSI on a price series."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # First average: simple mean over first `length` bars
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def run_simulation():
    # 2. DATA LOADING
    print(f"Loading {HISTORICAL_DATA_FILE}...")
    try:
        df_hist = pd.read_csv(HISTORICAL_DATA_FILE)
        df_hist["date"] = pd.to_datetime(df_hist["date"], utc=True).dt.tz_convert("US/Eastern")
        df_hist.set_index("date", inplace=True)
        df_hist.sort_index(inplace=True)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return

    # 3. RESAMPLE TO TARGET TIMEFRAME & COMPUTE RSI
    print(f"Resampling to {RSI_TIMEFRAME} and computing RSI({RSI_LENGTH})...")
    df_resampled = df_hist["close"].resample(RSI_TIMEFRAME).last().dropna()
    rsi = compute_rsi(df_resampled, RSI_LENGTH)

    # 4. GENERATE SIGNALS
    # A signal fires when RSI crosses into/through extreme territory.
    # We use a simple threshold cross: current bar crosses the level from the other side.
    prev_rsi = rsi.shift(1)
    buy_signals = rsi.index[(rsi <= RSI_OVERSOLD) & (prev_rsi > RSI_OVERSOLD)]
    sell_signals = rsi.index[(rsi >= RSI_OVERBOUGHT) & (prev_rsi < RSI_OVERBOUGHT)]

    signals = (
        pd.concat(
            [
                pd.DataFrame({"time": buy_signals, "direction": 1}),
                pd.DataFrame({"time": sell_signals, "direction": -1}),
            ]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    print(f"Signals found — Buy: {len(buy_signals)}, Sell: {len(sell_signals)}")

    if signals.empty:
        print("No RSI signals generated. Try adjusting RSI_OVERSOLD / RSI_OVERBOUGHT thresholds.")
        return

    # 5. SIMULATION ENGINE
    results = []
    current_time = pd.Timestamp.min.tz_localize("US/Eastern")
    daily_stats = {}

    print("Starting simulation...")
    for _, signal in signals.iterrows():
        signal_time = signal["time"]
        trade_date = signal_time.date()

        # Initialize daily tracking
        if trade_date not in daily_stats:
            daily_stats[trade_date] = 0.0

        # B. Stop Trading Filter
        if STOP_TRADING_DAILY:
            if daily_stats[trade_date] >= STOP_TRADING_ABOVE or daily_stats[trade_date] <= STOP_TRADING_BELOW:
                continue

        direction = signal["direction"]
        if FLIP_TRADE_DIRECTIONS:
            direction *= -1

        # Entry on the first 1-sec bar AFTER the signal candle closes.
        # signal_time is the left-edge label of the resampled bar, so the candle's
        # close price isn't known until signal_time + RSI_TIMEFRAME has elapsed.
        tf_offset = pd.tseries.frequencies.to_offset(RSI_TIMEFRAME)
        candle_close_time = signal_time + tf_offset

        # A. Trading Hours Filter (uses candle_close_time = actual entry point)
        entry_time_of_day = candle_close_time.strftime("%H:%M")
        if entry_time_of_day < START_TRADING_TIME or entry_time_of_day >= END_TRADING_TIME:
            continue

        # C. Sequence + Cooldown Filter (compare actual entry time, not signal left-edge)
        earliest_entry = current_time + pd.Timedelta(minutes=COOLDOWN_MINUTES)
        if candle_close_time < earliest_entry:
            continue

        future_data = df_hist.loc[candle_close_time:]
        if future_data.empty:
            continue

        actual_entry_time = future_data.index[0]
        assert actual_entry_time >= candle_close_time, (
            f"LOOK-AHEAD BIAS: entry at {actual_entry_time} is inside the signal candle "
            f"[{signal_time}, {candle_close_time})"
        )

        # Find the next opposite-direction signal so we can reverse into it
        if REVERSE_ON_SIGNAL:
            next_reverse = signals[(signals["time"] > signal_time) & (signals["direction"] != signal["direction"])]
            reverse_entry_time = (next_reverse.iloc[0]["time"] + tf_offset) if not next_reverse.empty else None
        else:
            reverse_entry_time = None

        slippage = SLIPPAGE_TICKS * TICK_SIZE
        entry1_price = future_data.iloc[0]["open"] + direction * slippage
        active_contracts = 1
        avg_price = entry1_price
        rungs_hit = 0
        exit_time, exit_price, outcome_type = None, 0.0, ""

        for idx, row in future_data.iterrows():
            high, low = row["high"], row["low"]

            # Reverse Signal Exit (closes current trade when opposite RSI signal fires)
            if reverse_entry_time is not None and idx >= reverse_entry_time:
                exit_time = idx
                exit_price = row["open"] - direction * slippage
                outcome_type = "Reverse"
                break

            # Ladder Logic (Scale-in)
            while rungs_hit < len(LADDER_RUNGS):
                next_rung_offset = LADDER_RUNGS[rungs_hit]
                trigger_price = entry1_price - (direction * next_rung_offset)

                if (direction == 1 and low <= trigger_price) or (direction == -1 and high >= trigger_price):
                    active_contracts += 1
                    rungs_hit += 1
                    fill_price = trigger_price + direction * slippage
                    avg_price = ((avg_price * (active_contracts - 1)) + fill_price) / active_contracts
                else:
                    break

            # Stop Loss
            if (direction == 1 and low <= avg_price - Y_STOP_LOSS_POINTS) or (
                direction == -1 and high >= avg_price + Y_STOP_LOSS_POINTS
            ):
                exit_time = idx
                exit_price = avg_price - (direction * Y_STOP_LOSS_POINTS) - direction * slippage
                outcome_type = "Loss"
                break

            # Take Profit
            if (direction == 1 and high >= avg_price + TP_POINTS) or (direction == -1 and low <= avg_price - TP_POINTS):
                exit_time = idx
                exit_price = avg_price + (direction * TP_POINTS) - direction * slippage
                outcome_type = "Win"
                break

        if exit_time is not None:
            pnl = (exit_price - avg_price) * active_contracts * ES_MULTIPLIER * direction
            results.append(
                {
                    "Date": trade_date,
                    "EntryTime": actual_entry_time,
                    "Outcome": outcome_type,
                    "Rungs": rungs_hit,
                    "PnL": pnl,
                }
            )
            daily_stats[trade_date] += pnl
            current_time = exit_time

    # 6. REPORTING
    if not results:
        print("No trades were processed.")
        return

    res_df = pd.DataFrame(results)

    daily_pnl = res_df.groupby("Date")["PnL"].sum()
    daily_counts = res_df.groupby("Date")["PnL"].count()

    print("\n" + "=" * 55)
    print(f"   DAILY PNL (Stop Trading is: {STOP_TRADING_DAILY})")
    print(f"   RSI({RSI_LENGTH}) on {RSI_TIMEFRAME} bars  |  OB:{RSI_OVERBOUGHT} OS:{RSI_OVERSOLD}")
    print("=" * 55)
    for date in daily_pnl.index:
        print(f"{date}   {daily_pnl[date]:>10,.2f}   ({daily_counts[date]} trades)")
    print("-" * 45)
    print(f"Total Net PnL:   ${res_df['PnL'].sum():,.2f}")
    print("Ladder Rungs:     " + ", ".join([f"{r} pts" for r in LADDER_RUNGS]))

    breakdown = res_df.groupby(["Rungs", "Outcome"]).size().unstack(fill_value=0)
    for col in ["Win", "Loss", "Reverse"]:
        if col not in breakdown.columns:
            breakdown[col] = 0

    print("\n" + "=" * 45)
    print("      STATISTICS BY LADDER RUNG")
    print("=" * 45)
    print(breakdown[["Win", "Loss", "Reverse"]].to_string())
    print("-" * 45)
    print(f"Total Trades:    {len(res_df)}")
    print(f"Win Rate:        {(res_df['Outcome'] == 'Win').mean()*100:.2f}%")
    print(f"Reversed:        {(res_df['Outcome'] == 'Reverse').sum()}")
    print("=" * 45)

    # --- Time-of-Day Breakdown ---
    # Bucket each trade by the hour of its entry (Eastern time)
    res_df["Hour"] = res_df["EntryTime"].dt.hour
    tod = res_df.groupby("Hour").agg(
        Trades=("PnL", "count"),
        PnL=("PnL", "sum"),
        Wins=("Outcome", lambda s: (s == "Win").sum()),
        Losses=("Outcome", lambda s: (s == "Loss").sum()),
        Reversed=("Outcome", lambda s: (s == "Reverse").sum()),
    )
    tod["WinRate"] = (tod["Wins"] / tod["Trades"] * 100).round(1)
    tod["AvgPnL"] = (tod["PnL"] / tod["Trades"]).round(2)

    print("\n" + "=" * 65)
    print("      PNL BY TIME OF DAY (entry hour, US/Eastern)")
    print("=" * 65)
    print(f"{'Hour':<6} {'Trades':>6} {'PnL':>10} {'AvgPnL':>8} {'W':>4} {'L':>4} {'Rev':>4} {'WR%':>6}")
    print("-" * 65)
    for hour, row in tod.iterrows():
        label = f"{hour:02d}:xx"
        print(
            f"{label:<6} {int(row['Trades']):>6} {row['PnL']:>10,.2f} {row['AvgPnL']:>8,.2f}"
            f" {int(row['Wins']):>4} {int(row['Losses']):>4} {int(row['Reversed']):>4} {row['WinRate']:>5.1f}%"
        )
    print("=" * 65)


if __name__ == "__main__":
    run_simulation()
