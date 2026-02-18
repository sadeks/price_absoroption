"""
Configuration for Futures Absorption/Momentum Monitor.
Edit these values to adjust detection sensitivity.
"""

from datetime import datetime

# IBKR Connection
HOST = "127.0.0.1"
PORT = 7496  # 7497 for TWS paper, 7496 for TWS live, 4002 for Gateway
CLIENT_ID = 99  # Different from main trading app to avoid conflicts

# Futures Contract
SYMBOL = "ES"  # Futures symbol
EXCHANGE = "CME"  # CME for ES/MES futures


def _get_front_month_expiry() -> str:
    """Get the current front-month quarterly expiry (YYYYMM).
    ES quarters: Mar(03), Jun(06), Sep(09), Dec(12)."""
    now = datetime.now()
    year, month = now.year, now.month
    quarters = [3, 6, 9, 12]
    for q in quarters:
        if month <= q:
            return f"{year}{q:02d}"
    # Past December -> next year's March
    return f"{year + 1}03"


EXPIRY = _get_front_month_expiry()

# Detection Thresholds
TICK_THRESHOLD = 10  # Max price move in points for absorption (price must stay within this)
MIN_PRICE_MOVE = 2  # Min price move in points for momentum (price must move at least this)
WINDOW_SECONDS = 3.0  # Rolling window for tape analysis
BASELINE_WINDOW_SECONDS = 60.0  # Longer window to calculate average delta
DELTA_MULTIPLIER = 3  # Trigger when delta is Nx the average (e.g., 2x or 3x)
MIN_DELTA = 500  # Minimum absolute delta in contracts to trigger (prevents low-volume noise)
COOLDOWN_SECONDS = 1.0  # Minimum seconds between signals of same type (prevents duplicates)

# Tick size (0.25 for ES futures)
TICK_SIZE = 0.25
