# Price and credit constants for the database operations
# These are now loaded from config.yaml for easier management

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

try:
    import yaml

    with open("config.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    pricing = config.get("pricing", {})

    # Initial credits for new users
    INITIAL_CREDITS = pricing.get("initial_credits", 100)

    # Price constants for different operations
    SKIP_PRICE = pricing.get("skip_price", 0)
    APPROVE_PRICE = pricing.get("approve_price", 1)
    DELETE_PRICE = pricing.get("delete_price", 1)

except Exception:  # noqa: BLE001
    # Fallback values if config loading fails
    INITIAL_CREDITS = 100
    SKIP_PRICE = 0
    APPROVE_PRICE = 1
    DELETE_PRICE = 1
