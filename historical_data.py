import csv

from ib_async import IB, Future, util


async def get_mes_data():
    ib = IB()
    await ib.connectAsync("127.0.0.1", 7496, clientId=1)

    contract = Future("MES", "202603", "CME")

    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr="2 W",
        barSizeSetting="1 min",
        whatToShow="TRADES",
        useRTH=True,
    )

    with open("mes_1min_2weeks.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow([bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume])
    print(f"Saved {len(bars)} bars to mes_1min_2weeks.csv")

    ib.disconnect()


util.run(get_mes_data())
