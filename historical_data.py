import asyncio
import csv
import sys
from datetime import datetime, timedelta

from ib_async import IB, Future, util


CHUNK_SECONDS = 1800  # 30 minutes (IB max for 1-sec bars)


def rth_bounds(date_str):
    """Return naive local start and end datetimes for RTH on a given YYYYMMDD date."""
    day = datetime.strptime(date_str, "%Y%m%d")

    start = day.replace(hour=9, minute=30, second=0)  # 9:30 ET (assuming TWS is set to ET)
    end = day.replace(hour=16, minute=0, second=0)  # 16:00 ET

    return start, end


def is_rth_local(dt):
    """Assume dt is in the same local timezone as TWS (ET)."""
    h, m = dt.hour, dt.minute
    return (h > 9 or (h == 9 and m >= 30)) and (h < 16 or (h == 16 and m <= 0))


async def get_mes_data(start_date: str, end_date: str):
    ib = IB()
    ib.RequestTimeout = 60
    await ib.connectAsync("127.0.0.1", 7496, clientId=1)

    # Qualify contract
    contract = Future("MES", "202603", "CME")
    [contract] = await ib.qualifyContractsAsync(contract)

    # Build list of RTH windows for each trading day (local time)
    windows = []
    day = datetime.strptime(start_date, "%Y%m%d")
    end_day = datetime.strptime(end_date, "%Y%m%d")

    while day <= end_day:
        if day.weekday() < 5:  # Mon–Fri
            start_local, end_local = rth_bounds(day.strftime("%Y%m%d"))
            windows.append((start_local, end_local))
        day += timedelta(days=1)

    print(f"Downloading MES 1-sec RTH data from {start_date} to {end_date}")

    all_bars = []

    # Loop through each day's RTH window
    for start_local, end_local in windows:
        cursor = end_local

        print(f"\n=== {start_local.date()} RTH window ===")
        print(f"Local window: {start_local} → {end_local}")

        while cursor > start_local:
            chunk_start = cursor - timedelta(seconds=CHUNK_SECONDS)
            end_str = cursor.strftime("%Y%m%d %H:%M:%S")

            print(f"Requesting chunk ending at {end_str}")

            bars = None
            for _ in range(3):
                try:
                    bars = await asyncio.wait_for(
                        ib.reqHistoricalDataAsync(
                            contract,
                            endDateTime=end_str,
                            durationStr=f"{CHUNK_SECONDS} S",
                            barSizeSetting="1 secs",
                            whatToShow="TRADES",
                            useRTH=False,
                            formatDate=1,
                        ),
                        timeout=30,
                    )
                except asyncio.TimeoutError:
                    print("  Request timed out, retrying in 15s...")
                    await asyncio.sleep(15)
                    continue
                if bars:
                    break
                print("  No data returned, retrying in 15s...")
                await asyncio.sleep(15)

            if bars:
                print(f"  Got {len(bars)} bars")
                all_bars.extend(bars)
            else:
                print(f"  FAILED after 3 attempts for chunk ending at {end_str}")

            cursor = chunk_start
            await asyncio.sleep(11)  # pacing

    # Sort + dedupe
    all_bars.sort(key=lambda b: b.date)
    seen = set()
    unique_bars = []
    for bar in all_bars:
        if bar.date not in seen:
            seen.add(bar.date)
            unique_bars.append(bar)

    # Filter to 9:30–16:00 local (ET if TWS is ET)
    filtered = [b for b in unique_bars if is_rth_local(b.date)]

    filename = f"mes_1sec_RTH_{start_date}_to_{end_date}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for bar in filtered:
            writer.writerow([bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume])

    print(f"\nSaved {len(filtered)} RTH bars to {filename}")

    ib.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python historical_data.py YYYYMMDD YYYYMMDD")
        sys.exit(1)

    util.run(get_mes_data(sys.argv[1], sys.argv[2]))
