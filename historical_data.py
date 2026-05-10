import asyncio
import csv
import sys
from datetime import datetime, timedelta

from ib_async import IB, Future, util

CHUNK_SECONDS = 1800  # 30 minutes (IB max for 1-sec bars)


def rth_bounds(date_str):
    """Return naive local start and end datetimes for RTH on a given YYYYMMDD date."""
    day = datetime.strptime(date_str, "%Y%m%d")

    start = day.replace(hour=9, minute=30, second=0)  # 9:30 ET = 8:30 CT
    end = day.replace(hour=16, minute=0, second=0)  # 16:00 ET = 15:00 CT

    return start, end


def is_rth_local(dt):
    """Assume dt is in CT (TWS timezone)."""
    h, m = dt.hour, dt.minute
    return (h > 8 or (h == 8 and m >= 30)) and (h < 15 or (h == 15 and m == 0))


async def get_mes_data(start_date: str, end_date: str):
    ib = IB()
    ib.RequestTimeout = 60
    await ib.connectAsync("127.0.0.1", 7497, clientId=5)

    # Qualify contract
    contract = Future("MES", "202606", "CME", includeExpired=True)
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

    filename = f"mes_1sec_RTH_{start_date}_to_{end_date}.csv"
    print(f"Downloading MES 1-sec RTH data from {start_date} to {end_date}")
    print(f"Writing to {filename} (chunks appear as they arrive, not sorted)")

    seen = set()
    total_written = 0

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])

        # Loop through each day's RTH window
        for start_local, end_local in windows:
            cursor = start_local

            print(f"\n=== {start_local.date()} RTH window ===")
            print(f"Local window: {start_local} → {end_local}")

            while cursor < end_local:
                chunk_end = min(cursor + timedelta(seconds=CHUNK_SECONDS), end_local)
                end_str = chunk_end.strftime("%Y%m%d %H:%M:%S")

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
                    written = 0
                    for bar in bars:
                        if bar.date not in seen and is_rth_local(bar.date):
                            seen.add(bar.date)
                            writer.writerow([bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume])
                            written += 1
                    f.flush()
                    total_written += written
                    print(f"  Got {len(bars)} bars, wrote {written} RTH bars (total so far: {total_written})")
                else:
                    print(f"  FAILED after 3 attempts for chunk ending at {end_str}")

                cursor = chunk_end
                await asyncio.sleep(11)  # pacing

    print(f"\nDone. Saved {total_written} RTH bars to {filename}")

    ib.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python historical_data.py YYYYMMDD YYYYMMDD")
        sys.exit(1)

    util.run(get_mes_data(sys.argv[1], sys.argv[2]))
