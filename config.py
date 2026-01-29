"""
Configuration for Absorption & Momentum Monitors.
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
DELTA_THRESHOLD = 50  # Min cumulative delta (contracts) to trigger alert
TICK_THRESHOLD = 1  # Max price move in ticks for absorption (1 tick = 0.25 pts)
WINDOW_SECONDS = 3.0  # Rolling window for tape analysis

# Rolling Average (relative volume detection)
BASELINE_WINDOW_SECONDS = 60.0  # Longer window to calculate "normal" volume
DELTA_MULTIPLIER = 1.5  # Trigger when delta is Nx the baseline average

# ES tick size (do not change)
TICK_SIZE = 0.25

# Momentum Monitor Specific
MOMENTUM_WINDOW_SECONDS = 5.0  # Longer window for momentum detection
MOMENTUM_TICK_THRESHOLD = 2  # Min price move in ticks for momentum (2 ticks = 1 pt)
