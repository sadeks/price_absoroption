#!/usr/bin/env python3
"""
Quick launcher for futures monitors using config.py settings.
Usage:
    python run.py              # runs absorption (default)
    python run.py absorption   # runs absorption
    python run.py momentum     # runs momentum
"""

import asyncio
import sys
from absorption import AbsorptionMonitor
from momentum import MomentumMonitor
import config

async def run_absorption():
    monitor = AbsorptionMonitor(
        host=config.HOST,
        port=config.PORT,
        client_id=config.CLIENT_ID,
        symbol=config.SYMBOL,
        expiry=config.EXPIRY,
        exchange=config.EXCHANGE,
        tick_threshold=config.TICK_THRESHOLD,
        window_seconds=config.WINDOW_SECONDS,
        baseline_window_seconds=config.BASELINE_WINDOW_SECONDS,
        delta_multiplier=config.DELTA_MULTIPLIER,
        min_delta=config.MIN_DELTA,
        cooldown_seconds=config.COOLDOWN_SECONDS,
    )
    await monitor.start_monitoring()

async def run_momentum():
    monitor = MomentumMonitor(
        host=config.HOST,
        port=config.PORT,
        client_id=config.CLIENT_ID - 1,
        symbol=config.SYMBOL,
        expiry=config.EXPIRY,
        exchange=config.EXCHANGE,
        min_price_move=config.MIN_PRICE_MOVE,
        window_seconds=config.WINDOW_SECONDS,
        baseline_window_seconds=config.BASELINE_WINDOW_SECONDS,
        delta_multiplier=config.DELTA_MULTIPLIER,
        min_delta=config.MIN_DELTA,
        cooldown_seconds=config.COOLDOWN_SECONDS,
    )
    await monitor.start_monitoring()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "absorption"

    if mode == "absorption":
        asyncio.run(run_absorption())
    elif mode == "momentum":
        asyncio.run(run_momentum())
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python run.py [absorption|momentum]")
        sys.exit(1)
