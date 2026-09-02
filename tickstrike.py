#!/usr/bin/env python3
"""
TickStrike - Tick Rate Spike Detector

Fires when the number of trades per second spikes above the rolling baseline.
Unlike momentum.py (which requires delta + price move), this triggers on
ANY burst of activity, then classifies the burst as bullish/bearish/mixed
based on the buy/sell volume ratio within that window.

Less sensitive than vanilla TickStrike via:
  - Higher multiplier threshold (3x default vs ~2x)
  - Minimum absolute tick count floor
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import subprocess
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ib_async import IB, Future, ContFuture


@dataclass
class TickData:
    timestamp: float
    price: float
    size: int
    side: str  # 'BUY' (at ask), 'SELL' (at bid), 'MID' (classified by tick direction)


class TickStrikeMonitor:
    """
    Monitors the tape for tick rate spikes.

    Core idea:
      - Count how many trades happen per window (tick rate)
      - Compare to rolling baseline tick rate
      - When current rate >> baseline, fire an alert
      - Classify the burst direction from buy/sell volume ratio
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7496,
        client_id: int = 99,
        symbol: str = "ES",
        expiry: str = "",
        exchange: str = "CME",
        window_seconds: float = 2.0,  # Short window to measure burst rate
        baseline_window_seconds: float = 60.0,  # Longer window for rolling average
        tick_rate_multiplier: float = 3.0,  # Trigger at Nx the average tick rate
        min_ticks_in_window: int = 15,  # Absolute floor — ignore noise in dead markets
        bias_threshold: float = 0.62,  # Buy% above this = BUY burst, below (1-this) = SELL burst
        alert_mixed: bool = False,  # Alert on mixed bursts (noisy, off by default)
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.symbol = symbol
        self.expiry = expiry
        self.exchange = exchange

        self.window_seconds = window_seconds
        self.baseline_window_seconds = baseline_window_seconds
        self.tick_rate_multiplier = tick_rate_multiplier
        self.min_ticks_in_window = min_ticks_in_window
        self.bias_threshold = bias_threshold
        self.alert_mixed = alert_mixed

        self.tick_size = 0.25  # ES futures

        # IB connection
        self.ib = IB()
        self.connected = False

        # Rolling windows
        self.tape: deque[TickData] = deque()  # Short window (burst detection)
        self.baseline_tape: deque[TickData] = deque()  # Long window (baseline rate)

        # State
        self.last_processed_idx = 0
        self.last_trade_price: float | None = None

        # Status print
        self.last_status_print: float = 0
        self.status_interval: float = 10.0

        # Sound files (reuse momentum sounds)
        self.sounds_dir = os.path.join(os.path.dirname(__file__), "sounds")
        self.buy_sound = os.path.join(self.sounds_dir, "buy_momentum.wav")
        self.sell_sound = os.path.join(self.sounds_dir, "sell_momentum.wav")

    async def connect(self) -> bool:
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self.connected = True
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected to IBKR")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            print("Disconnected from IBKR")

    async def _get_contract(self) -> Future:
        if self.expiry:
            contract = Future(self.symbol, self.expiry, self.exchange)
        else:
            contract = ContFuture(self.symbol, exchange=self.exchange)
        details = await self.ib.reqContractDetailsAsync(contract)
        if not details:
            raise ValueError(f"No contract found for {self.symbol}")
        resolved = details[0].contract
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] Using contract: {resolved.symbol} {resolved.lastTradeDateOrContractMonth}"
        )
        return resolved

    def _process_new_ticks(self, mkt_ticker, tbt_ticker):
        if not tbt_ticker.tickByTicks:
            return

        bid = mkt_ticker.bid if mkt_ticker.bid and mkt_ticker.bid > 0 else None
        ask = mkt_ticker.ask if mkt_ticker.ask and mkt_ticker.ask > 0 else None

        new_ticks = tbt_ticker.tickByTicks[self.last_processed_idx :]
        self.last_processed_idx = len(tbt_ticker.tickByTicks)

        for tick in new_ticks:
            if not hasattr(tick, "price") or not hasattr(tick, "size"):
                continue

            price = tick.price
            size = int(tick.size)

            if ask is not None and price >= ask:
                side = "BUY"
            elif bid is not None and price <= bid:
                side = "SELL"
            else:
                # Mid trade: classify by tick direction
                if self.last_trade_price is not None:
                    if price > self.last_trade_price:
                        side = "BUY"
                    elif price < self.last_trade_price:
                        side = "SELL"
                    else:
                        self.last_trade_price = price
                        continue
                else:
                    self.last_trade_price = price
                    continue

            self.last_trade_price = price
            td = TickData(timestamp=time.time(), price=price, size=size, side=side)
            self.tape.append(td)
            self.baseline_tape.append(td)

        self._prune_old_ticks()
        self._check_tickstrike()

    def _prune_old_ticks(self):
        cutoff = time.time() - self.window_seconds
        while self.tape and self.tape[0].timestamp < cutoff:
            self.tape.popleft()

        baseline_cutoff = time.time() - self.baseline_window_seconds
        while self.baseline_tape and self.baseline_tape[0].timestamp < baseline_cutoff:
            self.baseline_tape.popleft()

    def _calculate_tick_rate(self) -> int:
        """Number of ticks (trades) in the current short window."""
        return len(self.tape)

    def _calculate_avg_tick_rate(self) -> float:
        """Average number of ticks per window over the baseline period."""
        if len(self.baseline_tape) < 2:
            return 0.0

        start_time = self.baseline_tape[0].timestamp
        end_time = self.baseline_tape[-1].timestamp
        if end_time - start_time <= 0:
            return 0.0

        window_counts = []
        window_start = start_time
        while window_start < end_time:
            window_end = window_start + self.window_seconds
            count = sum(1 for t in self.baseline_tape if window_start <= t.timestamp < window_end)
            if count > 0:
                window_counts.append(count)
            window_start = window_end

        if not window_counts:
            return 0.0

        return sum(window_counts) / len(window_counts)

    def _classify_burst(self) -> tuple[str, float, int]:
        """
        Returns (direction, buy_pct, delta).
        direction is 'BUY', 'SELL', or 'MIXED'.
        """
        buy_vol = sum(t.size for t in self.tape if t.side == "BUY")
        sell_vol = sum(t.size for t in self.tape if t.side == "SELL")
        total_vol = buy_vol + sell_vol
        delta = buy_vol - sell_vol

        if total_vol == 0:
            return "MIXED", 0.5, 0

        buy_pct = buy_vol / total_vol

        if buy_pct >= self.bias_threshold:
            direction = "BUY"
        elif buy_pct <= (1.0 - self.bias_threshold):
            direction = "SELL"
        else:
            direction = "MIXED"

        return direction, buy_pct, delta

    def _check_tickstrike(self):
        if len(self.tape) < 2:
            return

        now = time.time()
        tick_rate = self._calculate_tick_rate()
        avg_rate = self._calculate_avg_tick_rate()
        current_price = self.tape[-1].price

        if avg_rate <= 0:
            if now - self.last_status_print >= self.status_interval:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Warming up baseline... ({len(self.baseline_tape)} ticks collected)"
                )
                self.last_status_print = now
            return

        multiple = tick_rate / avg_rate
        threshold = max(avg_rate * self.tick_rate_multiplier, self.min_ticks_in_window)

        # Status print
        if now - self.last_status_print >= self.status_interval:
            direction, buy_pct, delta = self._classify_burst()
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Ticks/window: {tick_rate} | "
                f"Avg: {avg_rate:.1f} | "
                f"Multiple: {multiple:.1f}x | "
                f"Threshold: {threshold:.0f} ({self.tick_rate_multiplier}x or min {self.min_ticks_in_window}) | "
                f"Buy%: {buy_pct*100:.0f}% | "
                f"Delta: {delta:+d}"
            )
            self.last_status_print = now

        if tick_rate < threshold:
            return

        direction, buy_pct, delta = self._classify_burst()

        if direction == "MIXED" and not self.alert_mixed:
            return

        self._fire_alert(direction, tick_rate, multiple, buy_pct, delta, current_price)

    def _fire_alert(self, direction: str, tick_rate: int, multiple: float, buy_pct: float, delta: int, price: float):
        timestamp = datetime.now().strftime("%H:%M:%S")

        if direction == "BUY":
            icon = "🟢"
            label = "BUY BURST"
            self._play_sound(self.buy_sound)
        elif direction == "SELL":
            icon = "🔴"
            label = "SELL BURST"
            self._play_sound(self.sell_sound)
        else:
            icon = "🟡"
            label = "MIXED BURST"

        print(
            f"[{timestamp}] {icon} {label} @ {price:.2f} | "
            f"Ticks: {tick_rate} ({multiple:.1f}x avg) | "
            f"Buy%: {buy_pct*100:.0f}% | "
            f"Delta: {delta:+d}"
        )

    def _play_sound(self, sound_file: str):
        try:
            if os.path.exists(sound_file):
                subprocess.Popen(["afplay", sound_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                fallback = (
                    "/System/Library/Sounds/Glass.aiff"
                    if "buy" in sound_file.lower()
                    else "/System/Library/Sounds/Basso.aiff"
                )
                subprocess.Popen(["afplay", fallback], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Could not play sound: {e}")

    async def start_monitoring(self):
        if not self.connected:
            if not await self.connect():
                return

        contract = await self._get_contract()
        mkt_ticker = self.ib.reqMktData(contract, "", False, False)
        tbt_ticker = self.ib.reqTickByTickData(contract, "AllLast")

        await asyncio.sleep(1)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] TickStrike monitoring {contract.symbol}...")
        print(f"  Tick rate threshold: {self.tick_rate_multiplier}x average OR min {self.min_ticks_in_window} ticks")
        print(f"  Window: {self.window_seconds}s | Baseline: {self.baseline_window_seconds}s")
        print(f"  Bias threshold: {self.bias_threshold*100:.0f}% | Mixed bursts: {'ON' if self.alert_mixed else 'OFF'}")
        print()

        try:
            while self.connected:
                self._process_new_ticks(mkt_ticker, tbt_ticker)
                await asyncio.sleep(0.05)
        except KeyboardInterrupt:
            print("\nStopping TickStrike...")
        finally:
            self.disconnect()


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="TickStrike - Tick Rate Spike Detector")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--client-id", type=int, default=99)
    parser.add_argument("--symbol", default="ES")
    parser.add_argument("--expiry", default="")
    parser.add_argument("--exchange", default="CME")
    parser.add_argument("--window", type=float, default=3.0, help="Burst detection window (seconds)")
    parser.add_argument("--baseline", type=float, default=120.0, help="Baseline rolling window (seconds)")
    parser.add_argument("--multiplier", type=float, default=3.0, help="Tick rate multiplier to trigger")
    parser.add_argument("--min-ticks", type=int, default=15, help="Min ticks in window to trigger")
    parser.add_argument("--bias", type=float, default=0.62, help="Buy%% above this = BUY burst (0.0-1.0)")
    parser.add_argument("--mixed", action="store_true", help="Also alert on mixed-direction bursts")

    args = parser.parse_args()

    monitor = TickStrikeMonitor(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        symbol=args.symbol,
        expiry=args.expiry,
        exchange=args.exchange,
        window_seconds=args.window,
        baseline_window_seconds=args.baseline,
        tick_rate_multiplier=args.multiplier,
        min_ticks_in_window=args.min_ticks,
        bias_threshold=args.bias,
        alert_mixed=args.mixed,
    )

    await monitor.start_monitoring()


if __name__ == "__main__":
    asyncio.run(main())
