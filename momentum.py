#!/usr/bin/env python3
"""
Momentum Monitor
Detects momentum/breakout patterns in ES futures by analyzing tape data.

Momentum Detection:
- Bullish Momentum: High positive delta (lots of buying) AND price going up
  → Buyers in control, breakout potential
- Bearish Momentum: High negative delta (lots of selling) AND price going down
  → Sellers in control, breakdown potential
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


class MomentumMonitor:
    """Monitors ES futures tape for momentum/breakout patterns."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7496,
        client_id: int = 98,
        symbol: str = "ES",
        exchange: str = "CME",
        delta_threshold: int = 100,
        tick_threshold: float = 2.0,
        window_seconds: float = 5.0,
        baseline_window_seconds: float = 60.0,
        delta_multiplier: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.symbol = symbol
        self.exchange = exchange

        # Detection thresholds
        self.delta_threshold = delta_threshold  # Minimum delta to trigger
        self.tick_threshold = tick_threshold  # Min price move (in ticks) to consider momentum
        self.window_seconds = window_seconds  # Rolling window size

        # Rolling average settings
        self.baseline_window_seconds = baseline_window_seconds
        self.delta_multiplier = delta_multiplier

        # ES tick size
        self.tick_size = 0.25

        # IB connection
        self.ib = IB()
        self.connected = False

        # Tape data - rolling window (short window for detection)
        self.tape: deque[TickData] = deque()

        # Status print timing
        self.last_status_print: float = 0
        self.status_interval: float = 10.0  # Print status every 10 seconds

        # Baseline tape - longer window for rolling average
        self.baseline_tape: deque[TickData] = deque()

        # Track processed ticks to avoid duplicates
        self.last_processed_idx = 0

        # Track last trade price for MID classification
        self.last_trade_price: float | None = None

        # Sound files
        self.sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")
        self.bullish_momentum_sound = os.path.join(self.sounds_dir, "bullish_momentum.wav")
        self.bearish_momentum_sound = os.path.join(self.sounds_dir, "bearish_momentum.wav")

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
        new_ticks = tbt_ticker.tickByTicks[self.last_processed_idx :]
        self.last_processed_idx = len(tbt_ticker.tickByTicks)

        for tick in new_ticks:
            if not hasattr(tick, "price") or not hasattr(tick, "size"):
                continue

            price = tick.price
            size = int(tick.size)

            # Classify the trade
            if ask is not None and price >= ask:
                side = "BUY"  # Aggressive buy at ask
            elif bid is not None and price <= bid:
                side = "SELL"  # Aggressive sell at bid
            else:
                # MID trade - classify by tick direction
                if self.last_trade_price is not None:
                    if price > self.last_trade_price:
                        side = "BUY"  # Uptick = buying pressure
                    elif price < self.last_trade_price:
                        side = "SELL"  # Downtick = selling pressure
                    else:
                        # Price unchanged - skip this trade
                        self.last_trade_price = price
                        continue
                else:
                    # No previous price to compare - skip
                    self.last_trade_price = price
                    continue

            self.last_trade_price = price

            tick_data = TickData(timestamp=time.time(), price=price, size=size, side=side)
            self.tape.append(tick_data)
            self.baseline_tape.append(tick_data)

        # Prune old ticks
        self._prune_old_ticks()

        # Check for momentum
        self._check_momentum()

    def _prune_old_ticks(self):
        """Remove ticks older than the window."""
        cutoff = time.time() - self.window_seconds
        while self.tape and self.tape[0].timestamp < cutoff:
            self.tape.popleft()

        # Prune baseline tape with longer window
        baseline_cutoff = time.time() - self.baseline_window_seconds
        while self.baseline_tape and self.baseline_tape[0].timestamp < baseline_cutoff:
            self.baseline_tape.popleft()

    def _calculate_delta(self) -> int:
        """Calculate cumulative delta (buys - sells) in the window."""
        buy_volume = sum(t.size for t in self.tape if t.side == "BUY")
        sell_volume = sum(t.size for t in self.tape if t.side == "SELL")
        return buy_volume - sell_volume

    def _calculate_baseline_avg_delta(self) -> float:
        """Calculate average delta magnitude per window over the baseline period."""
        if len(self.baseline_tape) < 2:
            return 0.0

        # Calculate total absolute delta over baseline period
        buy_volume = sum(t.size for t in self.baseline_tape if t.side == "BUY")
        sell_volume = sum(t.size for t in self.baseline_tape if t.side == "SELL")

        # Time span of baseline data
        time_span = self.baseline_tape[-1].timestamp - self.baseline_tape[0].timestamp
        if time_span <= 0:
            return 0.0

        # Average delta magnitude per window_seconds
        num_windows = time_span / self.window_seconds
        if num_windows < 1:
            num_windows = 1

        total_volume = buy_volume + sell_volume
        return total_volume / num_windows

    def _calculate_price_change(self) -> float:
        """Calculate price change in ticks over the window."""
        if len(self.tape) < 2:
            return 0.0

        first_price = self.tape[0].price
        last_price = self.tape[-1].price
        price_change = last_price - first_price
        return price_change / self.tick_size  # Convert to ticks

    def _check_momentum(self):
        """Check for momentum patterns and alert."""
        if len(self.tape) < 2:
            return

        delta = self._calculate_delta()
        price_change_ticks = self._calculate_price_change()
        current_price = self.tape[-1].price

        # Calculate dynamic threshold based on rolling average
        baseline_avg = self._calculate_baseline_avg_delta()
        current_volume = sum(t.size for t in self.tape)

        # Use the higher of: fixed threshold OR multiplier * baseline
        dynamic_threshold = max(
            self.delta_threshold,
            baseline_avg * self.delta_multiplier if baseline_avg > 0 else 0
        )

        # Print status periodically
        now = time.time()
        if now - self.last_status_print >= self.status_interval:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] Avg: {baseline_avg:.0f} | Threshold: {dynamic_threshold:.0f} | Current delta: {delta:+d} | Price Δ: {price_change_ticks:+.1f}")
            self.last_status_print = now

        # Momentum detection
        # Bullish Momentum: High positive delta AND price going up
        if delta >= dynamic_threshold and price_change_ticks >= self.tick_threshold:
            relative = f"{current_volume / baseline_avg:.1f}x avg" if baseline_avg > 0 else ""
            self._alert_bullish_momentum(delta, price_change_ticks, current_price, relative)

        # Bearish Momentum: High negative delta AND price going down
        elif delta <= -dynamic_threshold and price_change_ticks <= -self.tick_threshold:
            relative = f"{current_volume / baseline_avg:.1f}x avg" if baseline_avg > 0 else ""
            self._alert_bearish_momentum(delta, price_change_ticks, current_price, relative)

    def _alert_bullish_momentum(self, delta: int, price_change: float, price: float, extra: str = ""):
        """Alert for bullish momentum (buyers in control, price rising)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        extra_str = f" | {extra}" if extra else ""
        print(f"[{timestamp}] 🟢 BULLISH MOMENTUM @ {price:.2f} | Delta: +{delta} | Price Δ: {price_change:+.2f} ticks{extra_str}")
        self._play_sound(self.bullish_momentum_sound)

    def _alert_bearish_momentum(self, delta: int, price_change: float, price: float, extra: str = ""):
        """Alert for bearish momentum (sellers in control, price falling)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        extra_str = f" | {extra}" if extra else ""
        print(f"[{timestamp}] 🔴 BEARISH MOMENTUM @ {price:.2f} | Delta: {delta} | Price Δ: {price_change:+.2f} ticks{extra_str}")
        self._play_sound(self.bearish_momentum_sound)

    def _play_sound(self, sound_file: str):
        """Play alert sound (macOS)."""
        try:
            if os.path.exists(sound_file):
                subprocess.Popen(["afplay", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # Fallback to system sound
                if "bullish" in sound_file.lower():
                    # Bullish - glass clink sound
                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Glass.aiff"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    # Bearish - basso sound
                    subprocess.Popen(
                        ["afplay", "/System/Library/Sounds/Basso.aiff"],
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

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Monitoring {contract.localSymbol} tape for MOMENTUM...")
        print(f"  Delta threshold: {self.delta_threshold} contracts (or {self.delta_multiplier}x baseline)")
        print(f"  Tick threshold: {self.tick_threshold} ticks (min price move)")
        print(f"  Window: {self.window_seconds}s | Baseline: {self.baseline_window_seconds}s")
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

    parser = argparse.ArgumentParser(description="ES Futures Momentum Monitor")
    parser.add_argument("--host", default="127.0.0.1", help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=7497, help="TWS/Gateway port")
    parser.add_argument("--client-id", type=int, default=98, help="Client ID")
    parser.add_argument("--delta", type=int, default=100, help="Delta threshold (contracts)")
    parser.add_argument("--ticks", type=float, default=2.0, help="Min price tick movement")
    parser.add_argument("--window", type=float, default=5.0, help="Rolling window (seconds)")

    args = parser.parse_args()

    monitor = MomentumMonitor(
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
