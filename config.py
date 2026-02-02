"""
Configuration for Absorption Monitor.
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
TICK_THRESHOLD = 1  # Max price move in ticks for absorption (1 tick = 0.25 pts)
WINDOW_SECONDS = 3.0  # Rolling window for tape analysis
BASELINE_WINDOW_SECONDS = 60.0  # Longer window to calculate average delta
DELTA_MULTIPLIER = 3  # Trigger when delta is Nx the average (e.g., 2x or 3x)
MIN_DELTA = 100  # Minimum absolute delta to trigger (prevents low-volume noise overnight)
COOLDOWN_SECONDS = 1.0  # Minimum seconds between signals of same type (prevents duplicates)

# ES tick size (do not change)
TICK_SIZE = 0.25
