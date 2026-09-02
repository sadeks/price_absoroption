import asyncio
import csv
import sys
from datetime import datetime, timedelta

from ib_async import IB, Future, util

CHUNK_SECONDS = 1800  # 30 minutes (IB max for 1-sec bars)

# IB parses our naive `endDateTime` strings 1 hour ahead of the local (CT) time
# it actually stamps returned bars with (verified empirically: requesting
# endDateTime "09:30:00" returns bars timestamped 08:00:00-08:29:59). So the
# window we hand to reqHistoricalData must be 1 hour later than the CT window
# we actually want the data for.
REQUEST_TZ_SHIFT = timedelta(hours=1)


def rth_bounds(date_str):
    """Return naive `endDateTime`-request start/end for premarket (9:00-9:30 ET = 8:00-8:30 CT).

    These are literal request values (what IB expects in endDateTime); the
    actual CT window the returned bars will land in is this minus
    REQUEST_TZ_SHIFT (see is_in_window).
    """
    day = datetime.strptime(date_str, "%Y%m%d")

    start = day.replace(hour=8, minute=45, second=0)
    end = day.replace(hour=11, minute=30, second=0)

    return start, end


def is_in_window(dt, start_local, end_local):
    """dt (bar.date) comes back in CT; start_local/end_local are request-side, so shift back."""
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return start_local - REQUEST_TZ_SHIFT <= dt < end_local - REQUEST_TZ_SHIFT


async def get_mes_data(start_date: str, end_date: str):
    ib = IB()
    ib.RequestTimeout = 60
    await ib.connectAsync("127.0.0.1", 7496, clientId=5)

    # Qualify contract
    contract = Future("NQ", "202609", "CME", includeExpired=True)
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

    filename = f"mnq_1sec_RTH_{start_date}_to_{end_date}.csv"
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
                        if bar.date not in seen and is_in_window(bar.date, start_local, end_local):
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
