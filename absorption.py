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


@dataclass
class SignalTracker:
    """Tracks a signal's performance after triggering."""

    signal_type: str  # 'BUY' or 'SELL'
    entry_price: float
    entry_time: float
    max_profit_points: float = 0.0  # Max favorable move in points
    max_drawdown_points: float = 0.0  # Max adverse move in points
    delta: int = 0
    extra_info: str = ""


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
        self.tick_threshold = tick_threshold  # Max price move (in ticks) to consider absorption
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
        self.buy_absorption_sound = os.path.join(self.sounds_dir, "buy_absorption.wav")
        self.sell_absorption_sound = os.path.join(self.sounds_dir, "sell_absorption.wav")

        # Signal analytics tracking
        self.active_signals: list[SignalTracker] = []
        self.signal_track_duration: float = 300.0  # Track for 5 minutes after signal
        self.analytics_file = os.path.join(os.path.dirname(__file__), "absorption_analytics.txt")

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
        # Finalize any remaining active signals
        if self.active_signals:
            print(f"\n[Analytics] Finalizing {len(self.active_signals)} remaining signal(s)...")
            for signal in self.active_signals:
                self._finalize_signal(signal)
            self.active_signals.clear()

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

        # Check for absorption
        self._check_absorption()

        # Update signal analytics with current price
        if self.tape and self.active_signals:
            self._update_signal_analytics(self.tape[-1].price)

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

    def _check_absorption(self):
        """Check for absorption patterns and alert."""
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
            print(f"[{timestamp}] Avg: {baseline_avg:.0f} | Threshold: {dynamic_threshold:.0f} | Current delta: {delta:+d}")
            self.last_status_print = now

        # Delta-based absorption detection
        # Sell Absorption (bearish signal):
        # Lots of aggressive buying (positive delta) but price not going up
        if delta >= dynamic_threshold and price_change_ticks < self.tick_threshold:
            relative = f"{current_volume / baseline_avg:.1f}x avg" if baseline_avg > 0 else ""
            self._alert_sell_absorption(delta, price_change_ticks, current_price, relative)

        # Buy Absorption (bullish signal):
        # Lots of aggressive selling (negative delta) but price not going down
        elif delta <= -dynamic_threshold and price_change_ticks > -self.tick_threshold:
            relative = f"{current_volume / baseline_avg:.1f}x avg" if baseline_avg > 0 else ""
            self._alert_buy_absorption(delta, price_change_ticks, current_price, relative)

    def _alert_sell_absorption(self, delta: int, price_change: float, price: float, extra: str = ""):
        """Alert for sell absorption (sellers absorbing buys - bearish)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        extra_str = f" | {extra}" if extra else ""
        print(f"[{timestamp}] 🔴 SELL ABSORPTION @ {price:.2f} | Delta: +{delta} | Price Δ: {price_change:+.2f} ticks{extra_str}")
        self._play_sound(self.sell_absorption_sound)
        # Start tracking for analytics
        self._start_tracking_signal("SELL", price, delta, extra)

    def _alert_buy_absorption(self, delta: int, price_change: float, price: float, extra: str = ""):
        """Alert for buy absorption (buyers absorbing sells - bullish)."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        extra_str = f" | {extra}" if extra else ""
        print(f"[{timestamp}] 🟢 BUY ABSORPTION @ {price:.2f} | Delta: {delta} | Price Δ: {price_change:+.2f} ticks{extra_str}")
        self._play_sound(self.buy_absorption_sound)
        # Start tracking for analytics
        self._start_tracking_signal("BUY", price, delta, extra)

    def _update_signal_analytics(self, current_price: float):
        """Update all active signals with current price and finalize expired ones."""
        now = time.time()
        signals_to_finalize = []

        for signal in self.active_signals:
            # Calculate price move from entry
            price_move = current_price - signal.entry_price

            if signal.signal_type == "BUY":
                # BUY signal: profit when price goes up, drawdown when price goes down
                if price_move > signal.max_profit_points:
                    signal.max_profit_points = price_move
                if price_move < 0 and abs(price_move) > signal.max_drawdown_points:
                    signal.max_drawdown_points = abs(price_move)
            else:  # SELL signal
                # SELL signal: profit when price goes down, drawdown when price goes up
                if price_move < 0 and abs(price_move) > signal.max_profit_points:
                    signal.max_profit_points = abs(price_move)
                if price_move > 0 and price_move > signal.max_drawdown_points:
                    signal.max_drawdown_points = price_move

            # Check if signal tracking period has expired
            if now - signal.entry_time >= self.signal_track_duration:
                signals_to_finalize.append(signal)

        # Finalize expired signals
        for signal in signals_to_finalize:
            self._finalize_signal(signal)
            self.active_signals.remove(signal)

    def _finalize_signal(self, signal: SignalTracker):
        """Write final signal analytics to file."""
        entry_time_str = datetime.fromtimestamp(signal.entry_time).strftime("%Y-%m-%d %H:%M:%S")

        # Calculate profit/drawdown ratio
        if signal.max_drawdown_points > 0:
            ratio_str = f"{signal.max_profit_points / signal.max_drawdown_points:.2f}x"
        else:
            ratio_str = "N/A (no drawdown)"

        line = (
            f"{entry_time_str} | {signal.signal_type:4} | "
            f"Entry: {signal.entry_price:.2f} | "
            f"Delta: {signal.delta:+d} | "
            f"Max Profit: {signal.max_profit_points:.2f} pts | "
            f"Max Drawdown: {signal.max_drawdown_points:.2f} pts | "
            f"Profit/DD Ratio: {ratio_str}"
        )

        # Append to file
        try:
            with open(self.analytics_file, "a") as f:
                f.write(line + "\n")
            print(f"[Analytics] {signal.signal_type} signal finalized: Profit={signal.max_profit_points:.2f} pts, DD={signal.max_drawdown_points:.2f} pts")
        except Exception as e:
            print(f"[Analytics] Error writing to file: {e}")

    def _start_tracking_signal(self, signal_type: str, entry_price: float, delta: int, extra: str = ""):
        """Start tracking a new signal for analytics. Finalizes opposite signals only."""
        # Finalize signals of the OPPOSITE type when a new signal triggers
        opposite_type = "SELL" if signal_type == "BUY" else "BUY"
        signals_to_keep = []

        for signal in self.active_signals:
            if signal.signal_type == opposite_type:
                # Opposite signal - finalize it
                self._finalize_signal(signal)
            else:
                # Same signal type - keep tracking it
                signals_to_keep.append(signal)

        self.active_signals = signals_to_keep

        signal = SignalTracker(
            signal_type=signal_type,
            entry_price=entry_price,
            entry_time=time.time(),
            delta=delta,
            extra_info=extra,
        )
        self.active_signals.append(signal)

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
        print(f"  Delta threshold: {self.delta_threshold} contracts (or {self.delta_multiplier}x baseline)")
        print(f"  Tick threshold: {self.tick_threshold} ticks")
        print(f"  Window: {self.window_seconds}s | Baseline: {self.baseline_window_seconds}s")
        track_min = self.signal_track_duration / 60
        print(f"  Analytics: tracking signals for {track_min:.0f} min (or until next signal) -> {self.analytics_file}")
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
