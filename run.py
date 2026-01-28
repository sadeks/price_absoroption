#!/usr/bin/env python3
"""
Quick launcher for AbsorptionMonitor using config.py settings.
Usage: python run.py
"""

import asyncio
from monitor import AbsorptionMonitor
import config

async def main():
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

if __name__ == "__main__":
    asyncio.run(main())
