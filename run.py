#!/usr/bin/env python3
"""
Quick launcher for monitors using config.py settings.
Usage:
    python run.py absorption   # Run absorption monitor (default)
    python run.py momentum     # Run momentum monitor
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
        exchange=config.EXCHANGE,
        delta_threshold=config.DELTA_THRESHOLD,
        tick_threshold=config.TICK_THRESHOLD,
        window_seconds=config.WINDOW_SECONDS,
        baseline_window_seconds=config.BASELINE_WINDOW_SECONDS,
        delta_multiplier=config.DELTA_MULTIPLIER,
    )
    await monitor.start_monitoring()

async def run_momentum():
    monitor = MomentumMonitor(
        host=config.HOST,
        port=config.PORT,
        client_id=config.CLIENT_ID + 1,  # Different client ID to avoid conflicts
        symbol=config.SYMBOL,
        exchange=config.EXCHANGE,
        delta_threshold=config.DELTA_THRESHOLD,
        tick_threshold=config.MOMENTUM_TICK_THRESHOLD,
        window_seconds=config.MOMENTUM_WINDOW_SECONDS,
        baseline_window_seconds=config.BASELINE_WINDOW_SECONDS,
        delta_multiplier=config.DELTA_MULTIPLIER,
    )
    await monitor.start_monitoring()

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "absorption"

    if mode == "absorption":
        await run_absorption()
    elif mode == "momentum":
        await run_momentum()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python run.py [absorption|momentum]")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
