#!/usr/bin/env python3
"""
AggressiveBuySell Monitor
Detects passive absorption in ES futures by analyzing tape data.

Passive Absorption Detection:
- Sell Absorption (bearish): High positive delta (lots of buying) but price doesn't go up
  → Sellers are absorbing buy orders = resistance / bearish signal
- Buy Absorption (bullish): High negative delta (lots of selling) but price doesn't go down
  → Buyers are absorbing sell orders = support / bullish signal
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import subprocess
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ib_async import IB, Future


@dataclass
class TickData:
    """Single trade tick from the tape."""

    timestamp: float
    price: float
    size: int
    side: str  # 'BUY' (at ask), 'SELL' (at bid), or 'MID' (between)


class AbsorptionMonitor:
    """Monitors ES futures tape for passive absorption patterns."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7496,
        client_id: int = 99,
        symbol: str = "ES",
        exchange: str = "CME",
        delta_threshold: int = 100,
        tick_threshold: float = 0.5,
        window_seconds: float = 5.0,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.symbol = symbol
        self.exchange = exchange

        # Detection thresholds
        self.delta_threshold = delta_threshold  # Minimum delta to trigger
        self.tick_threshold = tick_threshold  # Max price move (in ticks) to consider absorption
        self.window_seconds = window_seconds  # Rolling window size

        # ES tick size
        self.tick_size = 0.25

        # IB connection
        self.ib = IB()
        self.connected = False

        # Tape data - rolling window
        self.tape: deque[TickData] = deque()

        # Track processed ticks to avoid duplicates
        self.last_processed_idx = 0

        # Sound files
        self.sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")
        self.buy_absorption_sound = os.path.join(self.sounds_dir, "buy_absorption.wav")
        self.sell_absorption_sound = os.path.join(self.sounds_dir, "sell_absorption.wav")

    async def connect(self) -> bool:
        """Connect to IBKR TWS/Gateway."""
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self.connected = True
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected to IBKR")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from IBKR."""
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            print("Disconnected from IBKR")

    async def _get_front_month_contract(self) -> Future:
        """Get the front month ES contract."""
        contract = Future(self.symbol, exchange=self.exchange)
        details = await self.ib.reqContractDetailsAsync(contract)
        if not details:
            raise ValueError(f"No contract found for {self.symbol}")

        # Sort by expiry and get front month
        sorted_details = sorted(details, key=lambda d: d.contract.lastTradeDateOrContractMonth)
        front_month = sorted_details[0].contract
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Using contract: {front_month.localSymbol}")
        return front_month

    def _process_new_ticks(self, mkt_ticker, tbt_ticker):
        """Process new tick-by-tick trades."""
        if not tbt_ticker.tickByTicks:
            return

        # Get current bid/ask from market data ticker
        bid = mkt_ticker.bid if mkt_ticker.bid and mkt_ticker.bid > 0 else None
        ask = mkt_ticker.ask if mkt_ticker.ask and mkt_ticker.ask > 0 else None

        # Process only new ticks
        new_ticks = tbt_ticker.tickByTicks[self.last_processed_idx:]
        self.last_processed_idx = len(tbt_ticker.tickByTicks)

        for tick in new_ticks:
            if not hasattr(tick, 'price') or not hasattr(tick, 'size'):
                continue

            price = tick.price
            size = int(tick.size)

            # Classify the trade
            side = "MID"
            if ask is not None and price >= ask:
                side = "BUY"  # Aggressive buy at ask
            elif bid is not None and price <= bid:
                side = "SELL"  # Aggressive sell at bid

            # Only track aggressive trades (ignore MID)
            if side == "MID":
                continue

            tick_data = TickData(timestamp=time.time(), price=price, size=size, side=side)
            self.tape.append(tick_data)

        # Prune old ticks
        self._prune_old_ticks()

        # Check for absorption
        self._check_absorption()

    def _prune_old_ticks(self):
        """Remove ticks older than the window."""
        cutoff = time.time() - self.window_seconds
        while self.tape and self.tape[0].timestamp < cutoff:
            self.tape.popleft()

    def _calculate_delta(self) -> int:
        """Calculate cumulative delta (buys - sells) in the window."""
        buy_volume = sum(t.size for t in self.tape if t.side == "BUY")
        sell_volume = sum(t.size for t in self.tape if t.side == "SELL")
        return buy_volume - sell_volume

    def _calculate_price_change(self) -> float:
        """Calculate price change in ticks over the window."""
        if len(self.tape) < 2:
            return 0.0

        first_price = self.tape[0].price
        last_price = self.tape[-1].price
        price_change = last_price - first_price
        return price_change / self.tick_size  # Convert to ticks

    def _check_absorption(self):
        """Check for absorption patterns and alert."""
        if len(self.tape) < 2:
            return

        delta = self._calculate_delta()
        price_change_ticks = self._calculate_price_change()

        current_price = self.tape[-1].price

        # Sell Absorption (bearish signal):
        # Lots of aggressive buying (positive delta) but price not going up
        # Sellers are absorbing the buy pressure = resistance
        if delta >= self.delta_threshold and price_change_ticks < self.tick_threshold:
            self._alert_sell_absorption(delta, price_change_ticks, current_price)

        # Buy Absorption (bullish signal):
        # Lots of aggressive selling (negative delta) but price not going down
        # Buyers are absorbing the sell pressure = support
        elif delta <= -self.delta_threshold and price_change_ticks > -self.tick_threshold:
            self._alert_buy_absorption(delta, price_change_ticks, current_price)

    def _alert_sell_absorption(self, delta: int, price_change: float, price: float):
        """Alert for sell absorption (sellers absorbing buys - bearish)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🔴 SELL ABSORPTION @ {price:.2f} | Delta: +{delta} | Price Δ: {price_change:+.2f} ticks")
        self._play_sound(self.sell_absorption_sound)

    def _alert_buy_absorption(self, delta: int, price_change: float, price: float):
        """Alert for buy absorption (buyers absorbing sells - bullish)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] 🟢 BUY ABSORPTION @ {price:.2f} | Delta: {delta} | Price Δ: {price_change:+.2f} ticks")
        self._play_sound(self.buy_absorption_sound)

    def _play_sound(self, sound_file: str):
        """Play alert sound (macOS)."""
        try:
            if os.path.exists(sound_file):
                subprocess.Popen(["afplay", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Fallback to system sound
                if "buy" in sound_file.lower():
                    # Bullish - upward heroic sound
                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Hero.aiff"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    # Bearish - low submarine dive sound
                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Submarine.aiff"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception as e:
            print(f"Could not play sound: {e}")

    async def start_monitoring(self):
        """Start monitoring the tape."""
        if not self.connected:
            if not await self.connect():
                return

        # Get front month contract
        contract = await self._get_front_month_contract()

        # Subscribe to market data for bid/ask
        mkt_ticker = self.ib.reqMktData(contract, "", False, False)

        # Subscribe to tick-by-tick trades
        tbt_ticker = self.ib.reqTickByTickData(contract, "AllLast")

        # Wait for initial data
        await asyncio.sleep(1)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitoring {contract.localSymbol} tape...")
        print(f"  Delta threshold: {self.delta_threshold} contracts")
        print(f"  Tick threshold: {self.tick_threshold} ticks")
        print(f"  Window: {self.window_seconds} seconds")
        print()

        # Main polling loop
        try:
            while self.connected:
                self._process_new_ticks(mkt_ticker, tbt_ticker)
                await asyncio.sleep(0.05)  # 50ms polling
        except KeyboardInterrupt:
            print("\nStopping monitor...")
        finally:
            self.disconnect()


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="ES Futures Absorption Monitor")
    parser.add_argument("--host", default="127.0.0.1", help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=7497, help="TWS/Gateway port")
    parser.add_argument("--client-id", type=int, default=99, help="Client ID")
    parser.add_argument("--delta", type=int, default=100, help="Delta threshold (contracts)")
    parser.add_argument("--ticks", type=float, default=0.5, help="Price tick threshold")
    parser.add_argument("--window", type=float, default=5.0, help="Rolling window (seconds)")

    args = parser.parse_args()

    monitor = AbsorptionMonitor(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        delta_threshold=args.delta,
        tick_threshold=args.ticks,
        window_seconds=args.window,
    )

    await monitor.start_monitoring()


if __name__ == "__main__":
    asyncio.run(main())
