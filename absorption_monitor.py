#!/usr/bin/env python3
"""
Absorption Monitor v2.2 — passive absorption alerts for ES/MES.

Concept
-------
Absorption = heavy aggressive flow in one direction while price refuses to move.

  BUY ABSORPTION  (bullish): heavy aggressive SELLING, price holds  -> passive buyer defending
  SELL ABSORPTION (bearish): heavy aggressive BUYING,  price holds  -> passive seller capping

Workflow: you draw structures by hand on your chart. When you hear an alert,
glance at where price is — absorption AT your structure boundary is the signal
(buy absorption at range bottom = likely bounce; sell absorption at range top
= likely rejection). Absorption in the middle of nowhere is ignorable.
YOU are the location filter; the tool just reads the tape.

Detection (all price quantities in POINTS):
  - |window delta| >= multiplier x baseline average  AND  >= min_delta contracts
  - window high-low RANGE <= flat_range_points (round-trips can't fake flatness)

Usage
-----
  python absorption_monitor.py                 # ES front month, defaults
  python absorption_monitor.py --symbol MES
  python absorption_monitor.py --min-delta 250 # less chatty
"""

import asyncio
import os
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from ib_async import IB, ContFuture, Future

# ---------------------------------------------------------------------------
# Single source of truth for all tunables. CLI flags map 1:1 onto these.
# All price quantities are in POINTS.
# ---------------------------------------------------------------------------
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 7496,
    "client_id": 99,
    "symbol": "ES",
    "expiry": "",  # blank -> ContFuture auto-resolves front month
    "exchange": "CME",
    "window_seconds": 5.0,  # detection window
    "baseline_seconds": 120.0,  # baseline window for average |delta|
    "flat_range_points": 0.75,  # window high-low range must be <= this (points)
    "delta_multiplier": 3.0,  # |delta| must be >= multiplier * baseline avg |delta|
    "min_delta": 150,  # ...AND >= this many contracts (noise floor)
    "cooldown_seconds": 20.0,  # per signal type
}


@dataclass
class Tick:
    ts: float
    price: float
    size: int
    side: str  # 'BUY' or 'SELL'


class AbsorptionMonitor:
    def __init__(self, **kw):
        cfg = {**DEFAULTS, **kw}
        for k, v in cfg.items():
            setattr(self, k, v)

        self.ib = IB()
        self.connected = False
        self.tape: deque[Tick] = deque()
        self.baseline: deque[Tick] = deque()
        self.last_trade_price: float | None = None
        self.last_side: str | None = None
        self.dropped_ticks = 0
        self.classified_by = {"quote": 0, "tick_rule": 0, "zero_tick": 0}

        self.last_signal_time = {"BUY": 0.0, "SELL": 0.0}

        self.last_status_print = 0.0
        self.status_interval = 15.0

        self.sounds_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")

    # ------------------------------------------------------------------ setup

    async def connect(self) -> bool:
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            self.connected = True
            print(f"[{self._now()}] Connected to IBKR")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    async def _get_contract(self):
        if self.expiry:
            contract = Future(self.symbol, self.expiry, self.exchange)
        else:
            contract = ContFuture(self.symbol, exchange=self.exchange)
        details = await self.ib.reqContractDetailsAsync(contract)
        if not details:
            raise ValueError(f"No contract found for {self.symbol}")
        resolved = details[0].contract
        print(f"[{self._now()}] Contract: {resolved.symbol} {resolved.lastTradeDateOrContractMonth}")
        return resolved

    # ------------------------------------------------------------------ tape

    def _on_pending_tickers(self, tickers):
        """Event handler: consume each tick-by-tick batch as it arrives.
        ib_async CLEARS ticker.tickByTicks after every emission, so batches
        must be consumed here — index-based polling silently drops data."""
        bid = self.mkt_ticker.bid if self.mkt_ticker.bid and self.mkt_ticker.bid > 0 else None
        ask = self.mkt_ticker.ask if self.mkt_ticker.ask and self.mkt_ticker.ask > 0 else None

        got_trades = False
        for ticker in tickers:
            for t in ticker.tickByTicks:
                if not hasattr(t, "price") or not hasattr(t, "size"):
                    continue
                price, size = t.price, int(t.size)
                if size <= 0:
                    continue

                if ask is not None and price >= ask:
                    side = "BUY"
                    self.classified_by["quote"] += 1
                elif bid is not None and price <= bid:
                    side = "SELL"
                    self.classified_by["quote"] += 1
                elif self.last_trade_price is not None and price != self.last_trade_price:
                    # tick rule: classify by direction of price change
                    side = "BUY" if price > self.last_trade_price else "SELL"
                    self.classified_by["tick_rule"] += 1
                elif self.last_side is not None:
                    # zero-tick rule (Lee-Ready): unchanged price inherits last side
                    side = self.last_side
                    self.classified_by["zero_tick"] += 1
                else:
                    # very first tick(s) with no quote and no history — can't classify
                    self.last_trade_price = price
                    self.dropped_ticks += 1
                    continue

                self.last_trade_price = price
                self.last_side = side
                tick = Tick(ts=time.time(), price=price, size=size, side=side)
                self.tape.append(tick)
                self.baseline.append(tick)
                got_trades = True

        if got_trades:
            self._prune()
            self._check_absorption()

    def _prune(self):
        now = time.time()
        cut = now - self.window_seconds
        while self.tape and self.tape[0].ts < cut:
            self.tape.popleft()
        bcut = now - self.baseline_seconds
        while self.baseline and self.baseline[0].ts < bcut:
            self.baseline.popleft()

    # ------------------------------------------------------------------ detection

    def _window_delta(self) -> int:
        return sum(t.size if t.side == "BUY" else -t.size for t in self.tape)

    def _window_range(self) -> float:
        """High-low range of the detection window, in points."""
        if len(self.tape) < 2:
            return 0.0
        prices = [t.price for t in self.tape]
        return max(prices) - min(prices)

    def _baseline_avg_abs_delta(self) -> float:
        """Average |delta| per window-length bucket over the baseline period."""
        if len(self.baseline) < 2:
            return 0.0
        start = self.baseline[0].ts
        end = self.baseline[-1].ts
        if end <= start:
            return 0.0
        deltas, w0 = [], start
        while w0 < end:
            w1 = w0 + self.window_seconds
            d, active = 0, False
            for t in self.baseline:
                if w0 <= t.ts < w1:
                    d += t.size if t.side == "BUY" else -t.size
                    active = True
            if active:
                deltas.append(abs(d))
            w0 = w1
        return sum(deltas) / len(deltas) if deltas else 0.0

    def _check_absorption(self):
        if len(self.tape) < 2:
            return

        delta = self._window_delta()
        rng = self._window_range()
        avg = self._baseline_avg_abs_delta()
        price = self.tape[-1].price
        now = time.time()

        threshold = max(avg * self.delta_multiplier if avg > 0 else float("inf"), self.min_delta)
        multiple = abs(delta) / avg if avg > 0 else 0.0

        if now - self.last_status_print >= self.status_interval:
            vol = sum(t.size for t in self.tape)
            total_cls = sum(self.classified_by.values())
            q_pct = (100 * self.classified_by["quote"] / total_cls) if total_cls else 0
            print(
                f"[{self._now()}] Δ {delta:+d} | vol {vol} ({len(self.tape)} trades/win) | "
                f"avg {avg:.0f} | {multiple:.1f}x | range {rng:.2f} pts | thr {threshold:.0f} | "
                f"quote-classified {q_pct:.0f}%"
            )
            if total_cls > 100 and q_pct < 50:
                print(
                    f"[{self._now()}] ⚠️  Most trades NOT classified by bid/ask — "
                    f"check market data subscription (delayed/missing quotes degrade accuracy)"
                )
            self.last_status_print = now

        if rng > self.flat_range_points:
            return  # price moved — not absorption by definition

        if delta >= threshold:
            sig_type = "SELL"  # heavy buying absorbed -> bearish
        elif delta <= -threshold:
            sig_type = "BUY"  # heavy selling absorbed -> bullish
        else:
            return

        if now - self.last_signal_time[sig_type] < self.cooldown_seconds:
            return
        self.last_signal_time[sig_type] = now

        emoji = "🟢" if sig_type == "BUY" else "🔴"
        print(
            f"[{self._now()}] {emoji} {sig_type} ABSORPTION @ {price:.2f} | "
            f"Δ {delta:+d} ({multiple:.1f}x) | range {rng:.2f} pts"
        )
        self._play_sound(sig_type)

    # ------------------------------------------------------------------ misc

    def _play_sound(self, sig_type: str):
        custom = os.path.join(self.sounds_dir, "buy_absorption.wav" if sig_type == "BUY" else "sell_absorption.wav")
        fallback = "/System/Library/Sounds/Hero.aiff" if sig_type == "BUY" else "/System/Library/Sounds/Submarine.aiff"
        path = custom if os.path.exists(custom) else fallback
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                print("\a", end="", flush=True)
        except Exception as e:
            print(f"Sound error: {e}")

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def disconnect(self):
        if self.connected:
            self.ib.disconnect()
            self.connected = False
            print("Disconnected from IBKR")

    # ------------------------------------------------------------------ main loop

    async def run(self):
        if not await self.connect():
            return
        contract = await self._get_contract()
        self.mkt_ticker = self.ib.reqMktData(contract, "", False, False)
        self.ib.reqTickByTickData(contract, "AllLast")
        self.ib.pendingTickersEvent += self._on_pending_tickers
        await asyncio.sleep(1)

        print(f"[{self._now()}] Monitoring {contract.symbol}")
        print(f"  Trigger: |Δ| >= {self.delta_multiplier}x baseline avg AND >= {self.min_delta} contracts")
        print(f"  Flat:    window range <= {self.flat_range_points} pts over {self.window_seconds}s\n")

        try:
            while self.connected:
                # ticks are handled by the event; this loop just keeps the
                # window honest during quiet stretches and prints status
                self._prune()
                self._check_absorption()
                await asyncio.sleep(1.0)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nStopping...")
        finally:
            self.ib.pendingTickersEvent -= self._on_pending_tickers
            self.disconnect()


def main():
    import argparse

    p = argparse.ArgumentParser(description="Absorption monitor (all units in points)")
    p.add_argument("--host", default=DEFAULTS["host"])
    p.add_argument("--port", type=int, default=DEFAULTS["port"])
    p.add_argument("--client-id", type=int, default=DEFAULTS["client_id"])
    p.add_argument("--symbol", default=DEFAULTS["symbol"])
    p.add_argument("--expiry", default=DEFAULTS["expiry"], help="YYYYMM; blank = front month")
    p.add_argument("--exchange", default=DEFAULTS["exchange"])
    p.add_argument("--window", type=float, default=DEFAULTS["window_seconds"], help="detection window seconds")
    p.add_argument("--baseline", type=float, default=DEFAULTS["baseline_seconds"], help="baseline window seconds")
    p.add_argument(
        "--flat-range", type=float, default=DEFAULTS["flat_range_points"], help="max window high-low range in POINTS"
    )
    p.add_argument(
        "--multiplier", type=float, default=DEFAULTS["delta_multiplier"], help="delta must be Nx baseline avg"
    )
    p.add_argument("--min-delta", type=int, default=DEFAULTS["min_delta"], help="minimum |delta| in contracts")
    p.add_argument("--cooldown", type=float, default=DEFAULTS["cooldown_seconds"])
    a = p.parse_args()

    mon = AbsorptionMonitor(
        host=a.host,
        port=a.port,
        client_id=a.client_id,
        symbol=a.symbol,
        expiry=a.expiry,
        exchange=a.exchange,
        window_seconds=a.window,
        baseline_seconds=a.baseline,
        flat_range_points=a.flat_range,
        delta_multiplier=a.multiplier,
        min_delta=a.min_delta,
        cooldown_seconds=a.cooldown,
    )
    asyncio.run(mon.run())


if __name__ == "__main__":
    main()
