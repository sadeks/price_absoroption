import pandas as pd

# 1. Configuration & Parameters
HISTORICAL_DATA_FILE = "mes_1sec_RTH_20260406_to_20260410.csv"
TRADES_FILE = "Trades_0406_0410.csv"

# HISTORICAL_DATA_FILE = "mes_1sec_RTH_20260202_to_20260313.csv"
# TRADES_FILE = "Trades_0202_0313.csv"


ES_MULTIPLIER = 50.0

# --- MULTI-RUNG LADDER PARAMETERS ---
# Add as many rungs as you like: [x1, x2, x3, ...]
LADDER_RUNGS = [5]  # Points against initial entry to add 1 contract
Y_STOP_LOSS_POINTS = 10  # Stop loss from AVERAGE price
TP_POINTS = 5  # Take profit from AVERAGE price

# --- GUARDRAIL FILTER ---
STOP_TRADING_DAILY = False  # Stop trading if daily PnL targets hit
STOP_TRADING_ABOVE = 5000.0  # Daily Profit Cap
STOP_TRADING_BELOW = -2000.0  # Daily Loss Floor

FLIP_TRADE_DIRECTIONS = False  # Flip Buy/Sell logic for testing

# --- TIME OF DAY FILTER ---
# None = all sessions, or a list of any combo: ["open", "midday", "afternoon"]
TIME_OF_DAY_FILTER = ["midday", "afternoon"]  # e.g. ["open", "afternoon"] or None

_SESSION_WINDOWS = {
    "open": ("09:30", "11:00"),
    "midday": ("11:00", "14:00"),
    "afternoon": ("14:00", "16:00"),
}


def run_simulation():
    # 2. DATA LOADING
    print(f"Loading {HISTORICAL_DATA_FILE}...")
    try:
        df_hist = pd.read_csv(HISTORICAL_DATA_FILE)
        df_hist["date"] = pd.to_datetime(df_hist["date"], utc=True).dt.tz_convert("US/Eastern")
        df_hist.set_index("date", inplace=True)
        df_hist.sort_index(inplace=True)

        print(f"Loading {TRADES_FILE}...")
        df_trades = pd.read_csv(TRADES_FILE)
        df_trades = df_trades[df_trades["Symbol"].astype(str).str.contains("ES|MES", na=False)]
        df_trades["DateTime"] = pd.to_datetime(df_trades["DateTime"], format="%Y%m%d;%H%M%S").dt.tz_localize(
            "US/Eastern"
        )
        df_trades.sort_values("DateTime", inplace=True)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        return

    # 3. SIMULATION ENGINE
    results = []
    current_time = pd.Timestamp.min.tz_localize("US/Eastern")
    daily_stats = {}  # Dictionary to track Date -> Cumulative PnL

    print("Starting simulation...")
    for _, trade in df_trades.iterrows():
        entry_time = trade["DateTime"]
        trade_date = entry_time.date()

        # Initialize daily tracking
        if trade_date not in daily_stats:
            daily_stats[trade_date] = 0.0

        # A. Stop Trading Filter
        if STOP_TRADING_DAILY is True:
            if daily_stats[trade_date] >= STOP_TRADING_ABOVE or daily_stats[trade_date] <= STOP_TRADING_BELOW:
                continue

        # B. Sequence Filter (Skip trades while already in a position)
        if entry_time < current_time:
            continue

        # C. Time of Day Filter
        if TIME_OF_DAY_FILTER is not None:
            t = entry_time.time()
            in_session = any(
                pd.Timestamp(_SESSION_WINDOWS[s][0]).time() <= t < pd.Timestamp(_SESSION_WINDOWS[s][1]).time()
                for s in TIME_OF_DAY_FILTER
                if s.lower() in _SESSION_WINDOWS
            )
            if not in_session:
                continue

        # FLIP IS HERE
        if FLIP_TRADE_DIRECTIONS is True:
            direction = 1 if str(trade["Buy/Sell"]).upper().startswith("S") else -1
        else:
            direction = 1 if str(trade["Buy/Sell"]).upper().startswith("B") else -1

        future_data = df_hist.loc[entry_time:]
        if future_data.empty:
            continue

        entry1_price = future_data.iloc[0]["open"]
        active_contracts = 1
        avg_price = entry1_price
        rungs_hit = 0
        exit_time, exit_price, outcome_type = None, 0.0, ""

        for idx, row in future_data.iterrows():
            high, low = row["high"], row["low"]

            # Ladder Logic (Scale-in)
            while rungs_hit < len(LADDER_RUNGS):
                next_rung_offset = LADDER_RUNGS[rungs_hit]
                trigger_price = entry1_price - (direction * next_rung_offset)

                if (direction == 1 and low <= trigger_price) or (direction == -1 and high >= trigger_price):
                    active_contracts += 1
                    rungs_hit += 1
                    # Recalculate average price
                    avg_price = ((avg_price * (active_contracts - 1)) + trigger_price) / active_contracts
                else:
                    break

            # Exit Logic (Stop Loss / Take Profit from Average)
            # Stop Loss
            if (direction == 1 and low <= avg_price - Y_STOP_LOSS_POINTS) or (
                direction == -1 and high >= avg_price + Y_STOP_LOSS_POINTS
            ):
                exit_time = idx
                exit_price = avg_price - (direction * Y_STOP_LOSS_POINTS)
                outcome_type = "Loss"
                break

            # Take Profit
            if (direction == 1 and high >= avg_price + TP_POINTS) or (direction == -1 and low <= avg_price - TP_POINTS):
                exit_time = idx
                exit_price = avg_price + (direction * TP_POINTS)
                outcome_type = "Win"
                break

        if exit_time is not None:
            pnl = (exit_price - avg_price) * active_contracts * ES_MULTIPLIER * direction
            results.append(
                {"Date": trade_date, "EntryTime": entry_time, "Outcome": outcome_type, "Rungs": rungs_hit, "PnL": pnl}
            )
            # Update cumulative daily PnL and move current time forward
            daily_stats[trade_date] += pnl
            current_time = exit_time

    # 4. REPORTING
    if not results:
        print("No trades were processed.")
        return

    res_df = pd.DataFrame(results)

    # Classify each trade into a named session by entry time (Eastern)
    def classify_session(ts):
        t = ts.time()
        if t < pd.Timestamp("09:30").time():
            return "Pre-Market  (04:00–09:30)"
        elif t < pd.Timestamp("11:00").time():
            return "Open        (09:30–11:00)"
        elif t < pd.Timestamp("14:00").time():
            return "Midday      (11:00–14:00)"
        else:
            return "Afternoon   (14:00–16:00)"

    res_df["Session"] = res_df["EntryTime"].apply(classify_session)

    # --- 4a. Daily PnL Report ---
    daily_pnl = res_df.groupby("Date")["PnL"].sum()

    print("\n" + "=" * 45)
    print(f"   DAILY PNL (Stop Trading is: {STOP_TRADING_DAILY})")
    print("=" * 45)
    print(daily_pnl.apply(lambda x: f"${x:,.2f}").to_string())
    print("-" * 45)
    print(f"Total Net PnL:   ${res_df['PnL'].sum():,.2f}")
    print("Ladder Rungs:     " + ", ".join([f"{r} pts" for r in LADDER_RUNGS]))
    # --- 4b. Statistics by Rung ---
    breakdown = res_df.groupby(["Rungs", "Outcome"]).size().unstack(fill_value=0)

    # Ensure both Win and Loss columns exist for formatting
    for col in ["Win", "Loss"]:
        if col not in breakdown.columns:
            breakdown[col] = 0

    print("\n" + "=" * 45)
    print("      STATISTICS BY LADDER RUNG")
    print("=" * 45)
    print(breakdown[["Win", "Loss"]].to_string())
    print("-" * 45)
    print(f"Total Trades:    {len(res_df)}")
    print(f"Win Rate:        {(res_df['Outcome'] == 'Win').mean()*100:.2f}%")
    print("=" * 45)

    # --- 4c. P&L by Session ---
    session_order = [
        "Pre-Market  (04:00–09:30)",
        "Open        (09:30–11:00)",
        "Midday      (11:00–14:00)",
        "Afternoon   (14:00–16:00)",
    ]
    session_group = res_df.groupby("Session")
    session_pnl = session_group["PnL"].sum()
    session_trades = session_group["PnL"].count()
    session_wins = session_group.apply(lambda g: (g["Outcome"] == "Win").sum())
    session_wr = (session_wins / session_trades * 100).round(1)

    print("\n" + "=" * 60)
    print("         P&L BY SESSION (Eastern Time)")
    print("=" * 60)
    print(f"{'Session':<30} {'Trades':>6}  {'Win%':>6}  {'Net PnL':>10}")
    print("-" * 60)
    for s in session_order:
        if s not in session_pnl.index:
            continue
        print(f"{s:<30} {int(session_trades[s]):>6}  {session_wr[s]:>5.1f}%  ${session_pnl[s]:>9,.2f}")
    print("-" * 60)
    print(
        f"{'TOTAL':<30} {len(res_df):>6}  {(res_df['Outcome']=='Win').mean()*100:>5.1f}%  ${res_df['PnL'].sum():>9,.2f}"
    )
    print("=" * 60)


if __name__ == "__main__":
    run_simulation()
