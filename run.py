#!/usr/bin/env python3
"""
Quick launcher for absorption monitor using config.py settings.
Usage:
    python run.py
"""

import asyncio
from absorption import AbsorptionMonitor
import config

async def run_absorption():
    monitor = AbsorptionMonitor(
        host=config.HOST,
        port=config.PORT,
        client_id=config.CLIENT_ID,
        symbol=config.SYMBOL,
        exchange=config.EXCHANGE,
        tick_threshold=config.TICK_THRESHOLD,
        window_seconds=config.WINDOW_SECONDS,
        baseline_window_seconds=config.BASELINE_WINDOW_SECONDS,
        delta_multiplier=config.DELTA_MULTIPLIER,
        min_delta=config.MIN_DELTA,
        cooldown_seconds=config.COOLDOWN_SECONDS,
    )
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(run_absorption())
