# This file is deprecated - configuration loading is now handled in config.py
# Remove this file as it's no longer used in the refactored architecture
import os
import sys
import yaml
from decimal import Decimal
from src.models.config import Config
import logging

logger = logging.getLogger('ConfigLoader')


# Backwards compatibility function
def load_config(config_path: str = None) -> Config:
    """
    Load configuration from environment variables or config file.
    This function is kept for backwards compatibility.
    New code should use load_config_from_yaml() directly.
    """
    print("WARNING: load_config() is deprecated. Use load_config_from_yaml() instead.")

    # Try to load from YAML first
    try:
        from src.models.config import load_config_from_yaml
        config = load_config_from_yaml()
        if config:
            return config
    except Exception as e:
        print(f"Failed to load from YAML: {e}")

    # Fallback to environment variables
    api_key = os.getenv('API_KEY')
    api_secret = os.getenv('API_SECRET')

    if not api_key or not api_secret:
        print("Error: API_KEY and API_SECRET environment variables must be set")
        sys.exit(1)

    config = Config(
        api_key=api_key,
        api_secret=api_secret,
        db_path=os.getenv('DB_PATH', 'market_maker.db'),
        log_file=os.getenv('LOG_FILE', 'market_maker.log'),
        exchange_id=os.getenv('EXCHANGE_ID', 'latoken'),
        symbol=os.getenv('SYMBOL', 'ATOM/USDT'),
        grid_levels=int(os.getenv('GRID_LEVELS', '3')),
        grid_spread=Decimal(os.getenv('GRID_SPREAD', '0.001')),
        min_order_size=Decimal(os.getenv('MIN_ORDER_SIZE', '0.1')),
        max_position=Decimal(os.getenv('MAX_POSITION', '0.5')),
        polling_interval=float(os.getenv('POLLING_INTERVAL', '30.0')),
        target_inventory_ratio=Decimal(os.getenv('TARGET_INVENTORY_RATIO', '0.5')),
        inventory_tolerance=Decimal(os.getenv('INVENTORY_TOLERANCE', '0.15')),
        max_orderbook_deviation=Decimal(os.getenv('MAX_ORDERBOOK_DEVIATION', '0.1')),
        outlier_filter_reference=os.getenv('OUTLIER_FILTER_REFERENCE', 'vwap'),
        out_of_range_pricing_fallback=os.getenv('OUT_OF_RANGE_PRICING_FALLBACK', 'true').lower() == 'true',
        out_of_range_price_mode=os.getenv('OUT_OF_RANGE_PRICE_MODE', 'vwap')
    )
    
    # Validate max_orderbook_deviation vs grid configuration
    if config.max_orderbook_deviation > 0:
        max_grid_spread = config.grid_spread * config.grid_levels
        if config.max_orderbook_deviation < max_grid_spread:
            logger.warning(
                f"⚠️  WARNING: max_orderbook_deviation ({float(config.max_orderbook_deviation):.3f}) is less than "
                f"the maximum grid spread ({float(max_grid_spread):.3f} = {config.grid_levels} levels × {float(config.grid_spread):.3f} spread). "
                f"This will cause the bot to filter out its own orders! "
                f"Consider setting max_orderbook_deviation to at least {float(max_grid_spread * Decimal('1.2')):.3f} "
                f"(20% above max grid spread) to avoid conflicts."
            )
    
    return config


if __name__ == '__main__':
    # Test loading config
    config = load_config()
    print(f"Config loaded: {config.symbol} on {config.exchange_id}")
