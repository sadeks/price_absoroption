"""
Configuration for AbsorptionMonitor.
Edit these values to adjust detection sensitivity.
"""

# IBKR Connection
HOST = "127.0.0.1"
PORT = 7496  # 7497 for TWS paper, 7496 for TWS live, 4002 for Gateway
CLIENT_ID = 99  # Different from main trading app to avoid conflicts

# Contract
SYMBOL = "ES"
EXCHANGE = "CME"

# Detection Thresholds
DELTA_THRESHOLD = 10  # Min cumulative delta (contracts) to trigger alert
TICK_THRESHOLD = 0.25  # Max price change (in ES ticks, 1 tick = 0.25 pts) to be "absorption"
WINDOW_SECONDS = 15.0  # Rolling window for tape analysis

# ES tick size (do not change)
TICK_SIZE = 0.25
