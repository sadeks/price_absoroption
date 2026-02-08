"""
Configuration for Stock Absorption Monitor.
Edit these values to adjust detection sensitivity.
"""

# IBKR Connection
HOST = "127.0.0.1"
PORT = 7496  # 7497 for TWS paper, 7496 for TWS live, 4002 for Gateway
CLIENT_ID = 99  # Different from main trading app to avoid conflicts

# Stock Contract
SYMBOL = "SPY"  # Stock ticker symbol
EXCHANGE = "SMART"  # SMART routing for best execution

# Detection Thresholds
TICK_THRESHOLD = 10  # Max price move in points for absorption (10 pts = $0.10)
WINDOW_SECONDS = 3.0  # Rolling window for tape analysis
BASELINE_WINDOW_SECONDS = 60.0  # Longer window to calculate average delta
DELTA_MULTIPLIER = 3  # Trigger when delta is Nx the average (e.g., 2x or 3x)
MIN_DELTA = 700  # Minimum absolute delta in shares to trigger (prevents low-volume noise)
COOLDOWN_SECONDS = 1.0  # Minimum seconds between signals of same type (prevents duplicates)

# Tick size (0.01 for most stocks)
TICK_SIZE = 0.01
