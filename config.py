from decimal import Decimal
from typing import Optional, Dict, Any
import os
import yaml


class Config:
    """Configuration container for the market maker bot."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        db_path: str,
        log_file: str,
        exchange_id: str,
        symbol: str,
        grid_levels: int,
        grid_spread: Decimal,
        min_order_size: Decimal,
        max_position: Decimal,
        polling_interval: float,
        target_inventory_ratio: Decimal,
        inventory_tolerance: Decimal,
        max_orderbook_deviation: Decimal = Decimal('0.1'),  # Default 10% max deviation
        outlier_filter_reference: str = 'vwap',  # Reference price for outlier filtering
        out_of_range_pricing_fallback: bool = True,  # Enable fallback pricing when out of range
        out_of_range_price_mode: str = 'vwap',  # Price mode when all orders filtered out
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.db_path = db_path
        self.log_file = log_file
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.grid_levels = grid_levels
        self.grid_spread = grid_spread
        self.min_order_size = min_order_size
        self.max_position = max_position
        self.polling_interval = polling_interval
        self.target_inventory_ratio = target_inventory_ratio
        self.inventory_tolerance = inventory_tolerance
        self.max_orderbook_deviation = max_orderbook_deviation
        self.outlier_filter_reference = outlier_filter_reference
        self.out_of_range_pricing_fallback = out_of_range_pricing_fallback
        self.out_of_range_price_mode = out_of_range_price_mode


def load_config_from_yaml() -> Optional[Config]:
    """Load configuration directly from YAML file."""
    try:
        print("Loading configuration from config.yaml...")

        if not os.path.exists('config.yaml'):
            print("Error: config.yaml file not found")
            return None

        with open('config.yaml', 'r') as file:
            config_data = yaml.safe_load(file)

        if not config_data:
            print("Error: config.yaml is empty or not valid YAML")
            return None

        # Extract API credentials
        api_config = config_data.get('api', {})
        api_key = api_config.get('key', '')
        api_secret = api_config.get('secret', '')

        if not api_key or not api_secret or api_key == "YOUR_API_KEY_HERE":
            print("Error: API key or secret missing or not configured in config.yaml")
            return None

        # Extract storage config
        storage_config = config_data.get('storage', {})
        db_path = storage_config.get('db_path', 'data/market_maker.db')
        log_file = storage_config.get('log_file', 'data/market_maker.log')

        # Extract bot config
        bot_config = config_data.get('bot_config', {})

        print(f"Config loaded successfully. Running bot for {bot_config.get('symbol', 'Unknown')}.")
        print(f"Using storage: DB={db_path}, Log={log_file}")
        print("API credentials: Set")

        return Config(
            api_key=api_key,
            api_secret=api_secret,
            db_path=db_path,
            log_file=log_file,
            exchange_id=bot_config.get('exchange_id', 'latoken'),
            symbol=bot_config.get('symbol', 'ATOM/USDT'),
            grid_levels=int(bot_config.get('grid_levels', 3)),
            grid_spread=Decimal(str(bot_config.get('grid_spread', '0.001'))),
            min_order_size=Decimal(str(bot_config.get('min_order_size', '0.1'))),
            max_position=Decimal(str(bot_config.get('max_position', '0.5'))),
            polling_interval=float(bot_config.get('polling_interval', 30.0)),
            target_inventory_ratio=Decimal(str(bot_config.get('target_inventory_ratio', '0.5'))),
            inventory_tolerance=Decimal(str(bot_config.get('inventory_tolerance', '0.15'))),
            max_orderbook_deviation=Decimal(str(bot_config.get('max_orderbook_deviation', '0.1'))),
            outlier_filter_reference=bot_config.get('outlier_filter_reference', 'vwap'),
            out_of_range_pricing_fallback=bot_config.get('out_of_range_pricing_fallback', True),
            out_of_range_price_mode=bot_config.get('out_of_range_price_mode', 'vwap')
        )

    except Exception as e:
        print(f"Error loading config: {e}")
        return None
