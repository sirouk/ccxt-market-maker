import asyncio
from decimal import Decimal, InvalidOperation, ConversionSyntax
import signal
import time
from typing import Dict, List, Tuple, Optional, Any

import ccxt.async_support as ccxt_async
from ccxt.base.errors import BaseError, InsufficientFunds
from sortedcontainers import SortedDict

from custom_logger import LoggerSetup
from database_manager import DatabaseManager
from config import load_config_from_yaml, Config
from retry_handler import RetryHandler
from order_manager import OrderManager
from _types import OrderRecord, OrderData


class MarketMakerREST:
    def __init__(self, config: Config):
        self.config = config
        self.logger = LoggerSetup.setup_logger('MarketMakerREST', config.log_file)
        self.db = DatabaseManager(config.db_path, config.log_file)

        exchange_config: Dict[str, Any] = {
            'enableRateLimit': True,
            'apiKey': config.api_key,
            'secret': config.api_secret
        }

        self.exchange = getattr(ccxt_async, config.exchange_id)(exchange_config)

        # Store partial in-memory orderbook for reference
        self.orderbook: Dict[str, SortedDict] = {
            'bids': SortedDict(),
            'asks': SortedDict()
        }

        # Basic flags
        self.running = False

        # Detect base/quote from "ETH/USDT" format
        self.base_currency, self.quote_currency = config.symbol.split('/')
        self.market_id: Optional[str] = None
        self.last_price: Optional[Decimal] = None  # Store last traded price
        self.ticker_bid: Optional[Decimal] = None  # Store ticker bid price
        self.ticker_ask: Optional[Decimal] = None  # Store ticker ask price

        # Initialize helper components
        self.retry_handler = RetryHandler(logger=self.logger)
        self.order_manager = OrderManager(
            exchange=self.exchange,
            symbol=config.symbol,
            db=self.db,
            logger=self.logger,
            retry_handler=self.retry_handler
        )

    def setup_signal_handlers(self) -> None:
        """Handle Ctrl+C or kill signals for graceful shutdown"""
        def signal_handler(signum: int, frame: Any) -> None:
            self.logger.info(f"Received shutdown signal {signum}")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    async def init_currency_ids(self) -> None:
        """Fetch market details from the exchange to get the market ID (if needed)."""
        try:
            markets = await self.exchange.fetch_markets()
            market = next((m for m in markets if m['symbol'] == self.config.symbol), None)
            if market is None:
                raise ValueError(f"Market {self.config.symbol} not found")
            self.market_id = market['id']
            self.logger.info(f"Initialized market {self.config.symbol} with ID {self.market_id}")
        except Exception as e:
            self.logger.error(f"Error initializing currency IDs: {e}")
            raise

    async def fetch_and_update_orderbook(self) -> None:
        """Fetch and update the orderbook, with outlier filtering if configured."""
        try:
            # First calculate current inventory ratio for potential directional bias
            if self.config.out_of_range_pricing_fallback:
                try:
                    self._last_inventory_ratio = await self.calculate_inventory_ratio()
                except Exception as e:
                    self.logger.warning(f"Could not calculate inventory ratio for directional bias: {e}")
            
            ob = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_order_book,
                "Fetch orderbook",
                self.config.symbol
            )
            
            # Get reference price for filtering outliers
            reference_price = None
            
            # Determine reference price based on configuration
            filter_ref = self.config.outlier_filter_reference.lower()
            
            if filter_ref == 'vwap':
                # First try to get VWAP (Volume Weighted Average Price) - most reliable
                try:
                    ticker = await self.retry_handler.retry_with_backoff(
                        self.exchange.fetch_ticker,
                        "Fetch ticker",
                        self.config.symbol
                    )
                    
                    # Store ticker data
                    ticker_bid = Decimal(str(ticker.get('bid', 0)))
                    ticker_ask = Decimal(str(ticker.get('ask', 0)))
                    
                    # Get VWAP as primary reference
                    vwap = ticker.get('vwap')
                    if vwap and vwap > 0:
                        reference_price = Decimal(str(vwap))
                        self.last_vwap = reference_price  # Store for later use
                        self.logger.info(f"Using VWAP for outlier filtering: {reference_price}")
                    
                    # Update last price and ticker values
                    last_price = ticker.get('last')
                    if last_price:
                        self.last_price = Decimal(str(last_price))
                    
                    self.ticker_bid = ticker_bid
                    self.ticker_ask = ticker_ask
                    
                except Exception as e:
                    self.logger.warning(f"Failed to fetch ticker: {e}")
                    
            elif filter_ref in ['nearest_bid', 'nearest_ask', 'ticker_mid']:
                # Fetch ticker for these modes
                try:
                    ticker = await self.retry_handler.retry_with_backoff(
                        self.exchange.fetch_ticker,
                        "Fetch ticker",
                        self.config.symbol
                    )
                    
                    ticker_bid = Decimal(str(ticker.get('bid', 0)))
                    ticker_ask = Decimal(str(ticker.get('ask', 0)))
                    
                    if filter_ref == 'nearest_bid' and ticker_bid > 0:
                        reference_price = ticker_bid
                        self.logger.info(f"Using ticker bid for outlier filtering: {reference_price}")
                    elif filter_ref == 'nearest_ask' and ticker_ask > 0:
                        reference_price = ticker_ask
                        self.logger.info(f"Using ticker ask for outlier filtering: {reference_price}")
                    elif filter_ref == 'ticker_mid' and ticker_bid > 0 and ticker_ask > 0:
                        reference_price = (ticker_bid + ticker_ask) / Decimal('2')
                        self.logger.info(f"Using ticker mid-price for outlier filtering: {reference_price}")
                    
                    # Store values for later use
                    self.ticker_bid = ticker_bid
                    self.ticker_ask = ticker_ask
                    
                    # Store VWAP if available for fallback
                    vwap = ticker.get('vwap')
                    if vwap and vwap > 0:
                        self.last_vwap = Decimal(str(vwap))
                    
                    # Update last price
                    last_price = ticker.get('last')
                    if last_price:
                        self.last_price = Decimal(str(last_price))
                        
                except Exception as e:
                    self.logger.warning(f"Failed to fetch ticker: {e}")
                    
            elif filter_ref == 'last':
                # Use last traded price
                try:
                    ticker = await self.retry_handler.retry_with_backoff(
                        self.exchange.fetch_ticker,
                        "Fetch ticker",
                        self.config.symbol
                    )
                    
                    last_price = ticker.get('last')
                    if last_price and last_price > 0:
                        reference_price = Decimal(str(last_price))
                        self.last_price = reference_price
                        self.logger.info(f"Using last price for outlier filtering: {reference_price}")
                    
                    # Store other values for later use
                    self.ticker_bid = Decimal(str(ticker.get('bid', 0)))
                    self.ticker_ask = Decimal(str(ticker.get('ask', 0)))
                    
                    vwap = ticker.get('vwap')
                    if vwap and vwap > 0:
                        self.last_vwap = Decimal(str(vwap))
                        
                except Exception as e:
                    self.logger.warning(f"Failed to fetch ticker: {e}")
            
            # Fallback if primary reference not available
            if not reference_price:
                # Try fallback options in order
                if hasattr(self, 'last_vwap') and self.last_vwap:
                    reference_price = self.last_vwap
                    self.logger.warning(f"Using stored VWAP as fallback for filtering: {reference_price}")
                elif hasattr(self, 'ticker_bid') and hasattr(self, 'ticker_ask') and self.ticker_bid > 0 and self.ticker_ask > 0:
                    spread_ratio = self.ticker_ask / self.ticker_bid
                    if spread_ratio < Decimal('10'):  # Only use if spread is reasonable
                        reference_price = (self.ticker_bid + self.ticker_ask) / Decimal('2')
                        self.logger.warning(f"Using ticker mid-price as fallback for filtering: {reference_price}")
                elif self.last_price:
                    reference_price = self.last_price
                    self.logger.warning(f"Using existing last price as fallback for filtering: {reference_price} (may be unreliable)")
            
            # If we have a valid reference price and max_orderbook_deviation is set, filter outliers
            if reference_price and reference_price > 0 and self.config.max_orderbook_deviation > 0:
                min_allowed_price = reference_price * (Decimal('1') - self.config.max_orderbook_deviation)
                max_allowed_price = reference_price * (Decimal('1') + self.config.max_orderbook_deviation)
                
                self.logger.debug(f"Filtering orderbook with reference_price={reference_price}, "
                                f"allowed range: {min_allowed_price} - {max_allowed_price}")
                
                # Filter bids and asks
                filtered_bids = {}
                filtered_asks = {}
                
                for b in ob['bids']:
                    price = Decimal(str(b[0]))
                    if min_allowed_price <= price <= max_allowed_price:
                        filtered_bids[price] = Decimal(str(b[1]))
                    else:
                        self.logger.debug(f"Filtered out bid at {price} (outside allowed range)")
                
                for a in ob['asks']:
                    price = Decimal(str(a[0]))
                    if min_allowed_price <= price <= max_allowed_price:
                        filtered_asks[price] = Decimal(str(a[1]))
                    else:
                        self.logger.debug(f"Filtered out ask at {price} (outside allowed range)")
                
                self.orderbook['bids'] = SortedDict(filtered_bids)
                self.orderbook['asks'] = SortedDict(filtered_asks)
                
                self.logger.info(f"Orderbook filtered: {len(filtered_bids)} bids, {len(filtered_asks)} asks "
                               f"(removed {len(ob['bids']) - len(filtered_bids)} outlier bids, "
                               f"{len(ob['asks']) - len(filtered_asks)} outlier asks)")
                
                # If orderbook is empty after filtering and directional bias is enabled
                if self.config.out_of_range_pricing_fallback and (not filtered_bids or not filtered_asks):
                    directional_price = self.get_directional_reference_price(ob)
                    if directional_price:
                        # Use directional price as the reference for empty side
                        if not filtered_bids:
                            # Create synthetic bid slightly below directional price
                            synthetic_bid_price = directional_price * Decimal('0.999')
                            self.orderbook['bids'][synthetic_bid_price] = Decimal('1')
                            self.logger.info(f"Added synthetic bid at {synthetic_bid_price} for directional rebalancing")
                        
                        if not filtered_asks:
                            # Create synthetic ask slightly above directional price
                            synthetic_ask_price = directional_price * Decimal('1.001')
                            self.orderbook['asks'][synthetic_ask_price] = Decimal('1')
                            self.logger.info(f"Added synthetic ask at {synthetic_ask_price} for directional rebalancing")
            else:
                # No filtering - use original orderbook
                self.orderbook['bids'] = SortedDict({
                    Decimal(str(b[0])): Decimal(str(b[1])) for b in ob['bids']
                })
                self.orderbook['asks'] = SortedDict({
                    Decimal(str(a[0])): Decimal(str(a[1])) for a in ob['asks']
                })

            # Debug logging for orderbook metrics
            if self.orderbook['bids'] and self.orderbook['asks']:
                best_bid_price, best_bid_volume = self.orderbook['bids'].peekitem(-1)
                best_ask_price, best_ask_volume = self.orderbook['asks'].peekitem(0)
                
                # Store best bid/ask for use in calculate_mid_price
                self.bid_price = best_bid_price
                self.ask_price = best_ask_price
                
                mid_price = (Decimal(str(best_bid_price)) + Decimal(str(best_ask_price))) / Decimal('2')
                spread = Decimal(str(best_ask_price)) - Decimal(str(best_bid_price))
                spread_pct = (spread / mid_price) * Decimal('100')

                self.logger.debug(f"Orderbook updated - Best bid: {best_bid_price} ({best_bid_volume}), "
                                f"Best ask: {best_ask_price} ({best_ask_volume}), "
                                f"Mid price: {mid_price}, Spread: {spread} ({spread_pct:.4f}%)")
            else:
                # Clear bid/ask if orderbook is empty
                self.bid_price = None
                self.ask_price = None
                self.logger.warning("Orderbook is empty after filtering")
        except Exception as e:
            self.logger.error(f"Error fetching orderbook: {e}")

    async def get_position(self) -> Decimal:
        """Fetch current total (base asset) position using REST."""
        try:
            balance = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_balance,
                "Fetch balance"
            )
            if balance and self.base_currency in balance:
                position = Decimal(str(balance[self.base_currency]['total']))
                self.logger.debug(f"Current {self.base_currency} position: {position}")
                return position
            self.logger.debug(f"No {self.base_currency} balance found, returning 0")
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"Error fetching position: {e}")
            return Decimal('0')

    async def get_quote_balance(self) -> Decimal:
        """Fetch current quote asset balance using REST."""
        try:
            balance = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_balance,
                "Fetch quote balance"
            )
            if balance and self.quote_currency in balance:
                quote_balance = Decimal(str(balance[self.quote_currency]['total']))
                self.logger.debug(f"Current {self.quote_currency} balance: {quote_balance}")
                return quote_balance
            self.logger.debug(f"No {self.quote_currency} balance found, returning 0")
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"Error fetching quote balance: {e}")
            return Decimal('0')

    async def get_available_balance(self, currency: str) -> Decimal:
        """Get available (free) balance for a specific currency."""
        try:
            balance = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_balance,
                f"Fetch available balance for {currency}"
            )
            if balance and currency in balance:
                # Use 'free' balance instead of 'total' to ensure we don't use funds tied up in orders
                available = Decimal(str(balance[currency]['free']))
                self.logger.debug(f"Available {currency} balance: {available}")
                return available
            return Decimal('0')
        except Exception as e:
            self.logger.error(f"Error fetching available balance for {currency}: {e}")
            return Decimal('0')

    async def validate_order_funds(self, side: str, price: Decimal, size: Decimal) -> Tuple[bool, Decimal]:
        """
        Validate if we have sufficient funds for the order and return adjusted size if needed.

        Returns:
            Tuple of (is_valid, adjusted_size)
        """
        try:
            self.logger.debug(f"Validating funds for {side} order: {size} @ {price}")

            if side == 'buy':
                # For buy orders, we need quote currency (e.g., USDT)
                available_quote = await self.get_available_balance(self.quote_currency)
                required_quote = price * size

                # Add small buffer for fees (typically 0.1-0.2%)
                fee_buffer = Decimal('1.002')  # 0.2% buffer
                required_with_buffer = required_quote * fee_buffer

                self.logger.debug(f"Buy order validation: available={available_quote} {self.quote_currency}, "
                                f"required={required_quote}, with_buffer={required_with_buffer}")

                if available_quote < required_with_buffer:
                    if available_quote < price:  # Can't even afford minimum
                        self.logger.warning(f"Insufficient {self.quote_currency} for buy order: have {available_quote}, need {required_with_buffer}")
                        return False, size

                    # Adjust size to fit available balance
                    max_affordable_size = (available_quote / fee_buffer) / price
                    adjusted_size = max(self.config.min_order_size, max_affordable_size)

                    if adjusted_size >= self.config.min_order_size:
                        self.logger.info(f"Adjusted buy order size from {size} to {adjusted_size} due to balance constraints")
                        return True, adjusted_size
                    else:
                        return False, size

                self.logger.debug(f"Buy order validation passed")
                return True, size

            else:  # sell
                # For sell orders, we need base currency (e.g., ATOM)
                available_base = await self.get_available_balance(self.base_currency)

                self.logger.debug(f"Sell order validation: available={available_base} {self.base_currency}, required={size}")

                if available_base < size:
                    if available_base < self.config.min_order_size:
                        self.logger.warning(f"Insufficient {self.base_currency} for sell order: have {available_base}, need {size}")
                        return False, size

                    # Adjust size to available balance
                    adjusted_size = max(self.config.min_order_size, available_base)
                    self.logger.info(f"Adjusted sell order size from {size} to {adjusted_size} due to balance constraints")
                    return True, adjusted_size

                self.logger.debug(f"Sell order validation passed")
                return True, size

        except Exception as e:
            self.logger.error(f"Error validating order funds: {e}")
            return False, size

    async def calculate_inventory_ratio(self) -> Decimal:
        """Calculate the current inventory ratio."""
        try:
            base_balance = await self.get_position()
            quote_balance = await self.get_quote_balance()

            # Use the new calculate_mid_price method that prioritizes VWAP
            mid_price = self.calculate_mid_price()
            
            if not mid_price:
                self.logger.warning("No valid price for inventory ratio, using target ratio")
                return self.config.target_inventory_ratio

            base_value = base_balance * mid_price
            total_value = base_value + quote_balance

            if total_value == Decimal('0'):
                self.logger.warning("Total portfolio value is zero, using target ratio")
                return self.config.target_inventory_ratio

            inventory_ratio = base_value / total_value

            self.logger.debug(f"Inventory metrics: base_balance={base_balance} {self.base_currency}, "
                            f"quote_balance={quote_balance} {self.quote_currency}, "
                            f"mid_price={mid_price}, base_value={base_value}, "
                            f"total_value={total_value}, inventory_ratio={inventory_ratio:.4f}, "
                            f"target_ratio={self.config.target_inventory_ratio}")

            return inventory_ratio

        except Exception as e:
            self.logger.error(f"Error calculating inventory ratio: {e}")
            return self.config.target_inventory_ratio

    def adjust_order_sizes_for_inventory(self, side: str, base_size: Decimal, inventory_ratio: Decimal) -> Decimal:
        """Adjust order sizes based on current inventory ratio vs target."""
        ratio_diff = inventory_ratio - self.config.target_inventory_ratio

        self.logger.debug(f"Inventory adjustment for {side}: base_size={base_size}, "
                        f"current_ratio={inventory_ratio:.4f}, target_ratio={self.config.target_inventory_ratio}, "
                        f"ratio_diff={ratio_diff:.4f}, tolerance={self.config.inventory_tolerance}")

        if abs(ratio_diff) <= self.config.inventory_tolerance:
            self.logger.debug(f"Within inventory tolerance, no adjustment needed")
            return base_size

        if ratio_diff > 0:  # Too much base currency
            if side == 'sell':
                adjustment = Decimal('1') + min(abs(ratio_diff), Decimal('0.5'))
                adjusted_size = base_size * adjustment
                self.logger.debug(f"Too much base currency, boosting sell order: {base_size} -> {adjusted_size} (adjustment={adjustment})")
                return adjusted_size
            else:  # buy side
                adjustment = max(Decimal('0.5'), Decimal('1') - abs(ratio_diff))
                adjusted_size = base_size * adjustment
                self.logger.debug(f"Too much base currency, reducing buy order: {base_size} -> {adjusted_size} (adjustment={adjustment})")
                return adjusted_size
        else:  # Too little base currency
            if side == 'buy':
                adjustment = Decimal('1') + min(abs(ratio_diff), Decimal('0.5'))
                adjusted_size = base_size * adjustment
                self.logger.debug(f"Too little base currency, boosting buy order: {base_size} -> {adjusted_size} (adjustment={adjustment})")
                return adjusted_size
            else:  # sell side
                adjustment = max(Decimal('0.5'), Decimal('1') - abs(ratio_diff))
                adjusted_size = base_size * adjustment
                self.logger.debug(f"Too little base currency, reducing sell order: {base_size} -> {adjusted_size} (adjustment={adjustment})")
                return adjusted_size

    async def calculate_order_grid(self) -> List[Tuple[str, Decimal, Decimal]]:
        """Build a list of potential orders around the mid-price."""
        try:
            # Use the new calculate_mid_price method that prioritizes VWAP
            mid_price = self.calculate_mid_price()
            
            if not mid_price:
                self.logger.error("Could not determine mid-price")
                return []
            
            # Sanity check: if we have a stored reference price, ensure mid_price is reasonable
            if self.last_price and self.last_price > 0:
                price_change_ratio = abs(mid_price - self.last_price) / self.last_price
                if price_change_ratio > Decimal('0.5'):  # More than 50% change
                    self.logger.warning(f"Extreme price movement detected! Mid-price: {mid_price}, Reference: {self.last_price}")
                    self.logger.warning(f"Price change: {price_change_ratio * 100:.1f}%")
                    
                    # Use the more conservative price
                    if mid_price > self.last_price * Decimal('2'):
                        self.logger.warning(f"Using reference price {self.last_price} instead of inflated mid-price {mid_price}")
                        mid_price = self.last_price

            # Get current balances to better size orders
            position = await self.get_position()
            available_base = await self.get_available_balance(self.base_currency)
            available_quote = await self.get_available_balance(self.quote_currency)

            current_inventory_ratio = await self.calculate_inventory_ratio()
            grid_orders: List[Tuple[str, Decimal, Decimal]] = []

            # Calculate total value for better sizing
            total_portfolio_value = (position * mid_price) + available_quote

            self.logger.debug(f"Grid calculation: mid_price={mid_price}, position={position}, "
                            f"available_base={available_base}, available_quote={available_quote}, "
                            f"total_portfolio_value={total_portfolio_value}, grid_levels={self.config.grid_levels}")

            for i in range(self.config.grid_levels):
                spread_pct = self.config.grid_spread * (i + 1)
                bid_price = mid_price * (Decimal('1') - spread_pct)
                ask_price = mid_price * (Decimal('1') + spread_pct)

                # More conservative base sizing based on available balances
                max_buy_size_by_balance = available_quote / (bid_price * Decimal('1.002'))  # Include fee buffer
                max_sell_size_by_balance = available_base

                # Use smaller base sizes and ensure we don't exceed available balances
                base_bid_size = min(
                    self.config.min_order_size * (i + 1),
                    max_buy_size_by_balance * Decimal('0.8'),  # Use only 80% of available
                    self.config.max_position * Decimal('0.2')  # Limit individual order size
                )

                base_ask_size = min(
                    self.config.min_order_size * (i + 1),
                    max_sell_size_by_balance * Decimal('0.8'),  # Use only 80% of available
                    self.config.max_position * Decimal('0.2')  # Limit individual order size
                )

                self.logger.debug(f"Grid level {i+1}: spread_pct={spread_pct:.4f}, "
                                f"bid_price={bid_price}, ask_price={ask_price}, "
                                f"base_bid_size={base_bid_size}, base_ask_size={base_ask_size}")

                # Adjust sizes based on inventory
                adjusted_bid_size = self.adjust_order_sizes_for_inventory('buy', base_bid_size, current_inventory_ratio)
                adjusted_ask_size = self.adjust_order_sizes_for_inventory('sell', base_ask_size, current_inventory_ratio)

                # Only add orders that meet minimum size and have sufficient balance
                if (adjusted_bid_size >= self.config.min_order_size and
                    available_quote >= bid_price * adjusted_bid_size * Decimal('1.002')):
                    grid_orders.append(('buy', bid_price, adjusted_bid_size))
                    self.logger.debug(f"Added buy order to grid: {adjusted_bid_size} @ {bid_price}")
                else:
                    self.logger.debug(f"Skipped buy order: size={adjusted_bid_size}, min_required={self.config.min_order_size}, "
                                    f"balance_check={available_quote >= bid_price * adjusted_bid_size * Decimal('1.002')}")

                if (adjusted_ask_size >= self.config.min_order_size and
                    available_base >= adjusted_ask_size):
                    grid_orders.append(('sell', ask_price, adjusted_ask_size))
                    self.logger.debug(f"Added sell order to grid: {adjusted_ask_size} @ {ask_price}")
                else:
                    self.logger.debug(f"Skipped sell order: size={adjusted_ask_size}, min_required={self.config.min_order_size}, "
                                    f"balance_check={available_base >= adjusted_ask_size}")

            self.logger.debug(f"Generated grid with {len(grid_orders)} orders")
            return grid_orders
        except Exception as e:
            self.logger.error(f"Error calculating grid: {e}")
            return []

    async def verify_order_placement(self, order_id: str, max_retries: int = 3) -> bool:
        """
        Verify that an order was successfully placed by checking if it exists in open orders.

        Returns:
            True if order is confirmed to exist, False otherwise
        """
        for attempt in range(max_retries):
            try:
                await asyncio.sleep(1)  # Wait a moment for order to appear

                open_orders = await self.retry_handler.retry_with_backoff(
                    self.exchange.fetch_open_orders,
                    f"Verify order placement {order_id}",
                    self.config.symbol
                )

                # Check if our order exists in the open orders
                for order in open_orders:
                    if str(order['id']) == order_id:
                        self.logger.debug(f"Order {order_id} confirmed in open orders")
                        return True

                # If not found, try fetching the specific order
                try:
                    order_details = await self.retry_handler.retry_with_backoff(
                        self.exchange.fetch_order,
                        f"Fetch order details {order_id}",
                        order_id,
                        self.config.symbol
                    )

                    if order_details and order_details.get('status') not in ['rejected', 'canceled', 'expired']:
                        self.logger.debug(f"Order {order_id} confirmed via direct fetch")
                        return True

                except Exception as e:
                    self.logger.debug(f"Could not fetch order {order_id} directly: {e}")

                self.logger.warning(f"Order {order_id} not found in verification attempt {attempt + 1}/{max_retries}")

            except Exception as e:
                self.logger.error(f"Error verifying order {order_id} on attempt {attempt + 1}: {e}")

        return False

    async def maybe_place_order(self, side: str, price: Decimal, size: Decimal) -> None:
        """Place an order if it doesn't duplicate existing orders."""
        self.logger.debug(f"Attempting to place {side} order: {size} @ {price}")

        # Avoid placing duplicates
        for order in self.order_manager.my_orders.values():
            if order['side'] == side:
                try:
                    order_price = Decimal(order['price'])
                    price_diff_pct = abs(order_price - price) / price
                    if price_diff_pct < Decimal('0.001'):
                        self.logger.debug(f"Skipping duplicate {side} order: existing at {order_price}, new at {price} (diff: {price_diff_pct:.6f})")
                        return
                except (InvalidOperation, ConversionSyntax, TypeError):
                    self.logger.warning(f"Could not compare order prices: {order['price']} vs {price}")
                    continue

        # Validate funds and adjust size if necessary
        is_valid, adjusted_size = await self.validate_order_funds(side, price, size)
        if not is_valid:
            self.logger.debug(f"Skipping {side} order due to insufficient funds: {size} @ {price}")
            return

        # Use adjusted size if it was modified
        if adjusted_size != size:
            self.logger.debug(f"Using adjusted size: {size} -> {adjusted_size}")
            size = adjusted_size

        try:
            order = await self.retry_handler.retry_with_backoff(
                self.exchange.create_order,
                f"Place {side} order",
                self.config.symbol,
                'limit',
                side,
                float(size),
                float(price)
            )
        except InsufficientFunds as e:
            # This should be rare now with our validation, but log it for analysis
            self.logger.warning(f"Still got insufficient funds error after validation for {side} order {size} @ {price}: {e}")
            return
        except BaseError as e:
            self.logger.error(f"Exchange error placing {side} order: {e}")
            return
        except Exception as e:
            self.logger.error(f"Unexpected error placing {side} order: {e}")
            return

        # Check if order is None or invalid
        if not order or not isinstance(order, dict):
            self.logger.error(f"Invalid order response: {order}")
            return

        # Extract order ID early for validation
        oid = str(order.get('id', ''))
        if not oid:
            self.logger.error(f"Order missing ID: {order}")
            return

        # Check order status with proper None handling
        order_status = order.get('status', '')
        if order_status and order_status.lower() in ['rejected', 'canceled', 'expired']:
            self.logger.error(f"Exchange returned a rejected/canceled/expired order {oid}: {order}")
            return

        # Verify order was actually placed successfully
        order_verified = await self.verify_order_placement(oid)
        if not order_verified:
            self.logger.error(f"Order {oid} could not be verified after placement, may have been rejected")
            return

        # Extract order fields with proper None handling
        order_side = order.get('side') or side
        order_side = order_side.lower() if order_side else side.lower()

        order_price = order.get('price', price)
        order_amount = order.get('amount', size)
        order_filled = order.get('filled', 0)
        order_status = order.get('status', 'open')
        order_info = order.get('info', {})

        # Track the successfully verified order
        self.order_manager.my_orders[oid] = OrderData(
            id=oid,
            symbol=order.get('symbol', self.config.symbol),
            price=str(order_price),
            side=order_side,
            amount=str(order_amount),
            filled=str(order_filled),
            status=order_status.upper() if order_status else 'OPEN',
            info=order_info if isinstance(order_info, dict) else {},
            created_at=time.time()
        )

        # Record in database using typed data structure
        order_record = OrderRecord(
            id=oid,
            pair=self.config.symbol,
            side=side,
            price=float(price),
            quantity=float(size)
        )
        self.db.record_order(order_record)
        self.logger.info(f"Successfully placed and verified {side} order: {size} @ {price}, ID: {oid}")

    async def market_making_loop(self) -> None:
        """Main market making loop."""
        consecutive_errors = 0
        max_consecutive_errors = 5
        loop_count = 0

        while self.running:
            try:
                loop_count += 1
                self.logger.debug(f"Starting market making loop iteration #{loop_count}")

                await self.fetch_and_update_orderbook()
                await self.order_manager.fetch_open_orders()

                # Log current order status
                open_orders_count = len(self.order_manager.my_orders)
                recently_closed_count = len(self.order_manager.recently_closed_orders)
                self.logger.debug(f"Order status: {open_orders_count} open orders, {recently_closed_count} recently closed orders")

                grid_orders = await self.calculate_order_grid()
                self.logger.debug(f"Generated {len(grid_orders)} potential grid orders")

                placed_orders = 0
                for side, price, size in grid_orders:
                    await self.maybe_place_order(side, price, size)
                    placed_orders += 1

                self.logger.debug(f"Loop #{loop_count} completed: processed {len(grid_orders)} grid orders")
                consecutive_errors = 0
                await asyncio.sleep(self.config.polling_interval)

            except Exception as e:
                consecutive_errors += 1
                self.logger.error(f"Error in market making loop (error #{consecutive_errors}): {e}")

                if consecutive_errors >= max_consecutive_errors:
                    self.logger.error(f"Too many consecutive errors ({consecutive_errors}), stopping bot")
                    self.running = False
                    break

                error_delay = min(5 * (2 ** (consecutive_errors - 1)), 60)
                self.logger.info(f"Waiting {error_delay}s before retry due to error...")
                await asyncio.sleep(error_delay)

    async def run(self) -> None:
        """Top-level entry point."""
        self.running = True
        self.setup_signal_handlers()

        try:
            self.logger.info(f"Starting REST-based market maker for {self.config.symbol}")
            await self.init_currency_ids()

            loop_task = asyncio.create_task(self.market_making_loop())
            done, pending = await asyncio.wait([loop_task], return_when=asyncio.FIRST_EXCEPTION)

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        except Exception as e:
            self.logger.error(f"Fatal error in run: {e}")
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        """Perform graceful shutdown."""
        self.running = False
        self.logger.info("Shutting down market maker...")

        try:
            await asyncio.wait_for(self.order_manager.cancel_all_orders(), timeout=30.0)
            self.logger.info("All orders cancelled successfully")
        except asyncio.TimeoutError:
            self.logger.error("Timeout while cancelling orders")
        except Exception as e:
            self.logger.error(f"Error during order cancellation: {e}")

        try:
            await asyncio.wait_for(self.exchange.close(), timeout=10.0)
            self.logger.info("Exchange connection closed")
        except Exception as e:
            self.logger.error(f"Error closing exchange: {e}")

        self.logger.info("Market maker stopped.")

    def calculate_mid_price(self) -> Optional[Decimal]:
        """Calculate mid-price with multiple fallback options"""
        # Option 1: Use orderbook mid-price if available (most reliable)
        if hasattr(self, 'bid_price') and hasattr(self, 'ask_price') and self.bid_price and self.ask_price:
            mid_price = (self.bid_price + self.ask_price) / Decimal('2')
            self.logger.info(f"Using orderbook mid-price: {mid_price}")
            return mid_price
        
        # If orderbook is filtered empty, check if fallback is enabled
        if not self.config.out_of_range_pricing_fallback:
            self.logger.warning("All orders filtered out and fallback pricing disabled")
            return None
            
        # Use configured out-of-range price mode
        price_mode = self.config.out_of_range_price_mode.lower()
        self.logger.info(f"All orders out of range, using fallback price mode: {price_mode}")
        
        # Get reference price for nearest bid/ask modes
        reference_price = None
        try:
            # Try to use stored VWAP from last fetch_and_update_orderbook call
            if hasattr(self, 'ticker_bid') and hasattr(self, 'ticker_ask'):
                # We already have ticker data from fetch_and_update_orderbook
                vwap = getattr(self, 'last_vwap', None)
                if vwap and vwap > 0:
                    reference_price = vwap
                    self.logger.debug(f"Using stored VWAP for price calculation: {reference_price}")
        except Exception as e:
            self.logger.warning(f"Failed to get reference price: {e}")
            
        # Option 2: Use configured price mode
        if price_mode == 'nearest_bid':
            if reference_price and hasattr(self, 'orderbook'):
                nearest_bid = self.get_nearest_valid_bid(reference_price)
                if nearest_bid:
                    self.logger.info(f"Using nearest bid price: {nearest_bid}")
                    return nearest_bid
                else:
                    self.logger.warning("No valid bid found, falling back to VWAP")
                    
        elif price_mode == 'nearest_ask':
            if reference_price and hasattr(self, 'orderbook'):
                nearest_ask = self.get_nearest_valid_ask(reference_price)
                if nearest_ask:
                    self.logger.info(f"Using nearest ask price: {nearest_ask}")
                    return nearest_ask
                else:
                    self.logger.warning("No valid ask found, falling back to VWAP")
        
        elif price_mode == 'vwap':
            # Use VWAP if available
            if reference_price:
                self.logger.info(f"Using VWAP: {reference_price}")
                return reference_price
        
        elif price_mode == 'auto':
            # Auto mode: Try full fallback hierarchy
            self.logger.info("Using auto mode - trying all price sources")
            
            # First try VWAP
            if reference_price:
                self.logger.info(f"Auto mode: Using VWAP: {reference_price}")
                return reference_price
            
            # Try ticker bid/ask
            try:
                ticker_bid = self.ticker_bid if hasattr(self, 'ticker_bid') else None
                ticker_ask = self.ticker_ask if hasattr(self, 'ticker_ask') else None
                if ticker_bid and ticker_ask and ticker_bid > 0 and ticker_ask > 0:
                    spread_ratio = ticker_ask / ticker_bid
                    if spread_ratio < Decimal('10'):  # Spread less than 10x
                        mid_price = (ticker_bid + ticker_ask) / Decimal('2')
                        self.logger.info(f"Auto mode: Using ticker bid/ask mid-price: {mid_price}")
                        return mid_price
                    else:
                        self.logger.warning(f"Auto mode: Ticker spread too wide (ratio: {spread_ratio})")
            except:
                pass
            
            # Try last price
            if self.last_price and self.last_price > 0:
                self.logger.warning(f"Auto mode: Using last price: {self.last_price}")
                return self.last_price
        
        # Final fallback for all modes (except when explicitly handled above)
        if price_mode != 'auto':
            # For non-auto modes, still try fallback options
            try:
                # Try stored ticker bid/ask
                ticker_bid = self.ticker_bid if hasattr(self, 'ticker_bid') else None
                ticker_ask = self.ticker_ask if hasattr(self, 'ticker_ask') else None
                if ticker_bid and ticker_ask and ticker_bid > 0 and ticker_ask > 0:
                    spread_ratio = ticker_ask / ticker_bid
                    if spread_ratio < Decimal('10'):  # Spread less than 10x
                        mid_price = (ticker_bid + ticker_ask) / Decimal('2')
                        self.logger.info(f"Using ticker bid/ask mid-price: {mid_price}")
                        return mid_price
                    else:
                        self.logger.warning(f"Ticker spread too wide (ratio: {spread_ratio})")
                        
                # Last resort: use last price
                if self.last_price and self.last_price > 0:
                    self.logger.warning(f"Using last price as final fallback: {self.last_price}")
                    return self.last_price
                    
            except Exception as e:
                self.logger.error(f"Failed to calculate mid-price: {e}")
        
        # Try stored last_price as absolute final fallback
        if self.last_price and self.last_price > 0:
            self.logger.warning(f"Using stored last price: {self.last_price}")
            return self.last_price
            
        self.logger.error("Could not determine any valid price")
        return None

    def get_directional_reference_price(self, ob: Dict) -> Optional[Decimal]:
        """
        Get reference price favoring the direction that helps inventory rebalancing.
        When we need to buy more (low inventory), favor asks.
        When we need to sell more (high inventory), favor bids.
        """
        try:
            # First check if we have current inventory ratio
            if not hasattr(self, '_last_inventory_ratio'):
                self._last_inventory_ratio = self.config.target_inventory_ratio
            
            current_ratio = self._last_inventory_ratio
            target_ratio = self.config.target_inventory_ratio
            ratio_diff = current_ratio - target_ratio
            
            # Determine which direction to favor
            favor_bids = ratio_diff > self.config.inventory_tolerance  # Too much base, need to sell
            favor_asks = ratio_diff < -self.config.inventory_tolerance  # Too little base, need to buy
            
            if not favor_bids and not favor_asks:
                # Within tolerance, no preference
                return None
            
            # Get all bids and asks with prices
            all_bids = [(Decimal(str(b[0])), Decimal(str(b[1]))) for b in ob.get('bids', [])]
            all_asks = [(Decimal(str(a[0])), Decimal(str(a[1]))) for a in ob.get('asks', [])]
            
            if favor_bids and all_bids:
                # Sort bids by price descending (best bid first)
                all_bids.sort(key=lambda x: x[0], reverse=True)
                # Return the best bid as reference
                best_bid = all_bids[0][0]
                self.logger.info(f"Favoring bid side for rebalancing (too much base): using {best_bid}")
                return best_bid
                
            elif favor_asks and all_asks:
                # Sort asks by price ascending (best ask first)
                all_asks.sort(key=lambda x: x[0])
                # Return the best ask as reference
                best_ask = all_asks[0][0]
                self.logger.info(f"Favoring ask side for rebalancing (too little base): using {best_ask}")
                return best_ask
            
            return None
            
        except Exception as e:
            self.logger.warning(f"Error getting directional reference price: {e}")
            return None

    def get_nearest_valid_bid(self, reference_price: Decimal) -> Optional[Decimal]:
        """
        Get the nearest valid bid price within acceptable deviation.
        Returns the highest bid that's within max_orderbook_deviation of reference price.
        """
        if not hasattr(self, 'orderbook') or not self.orderbook.get('bids'):
            return None
            
        max_deviation = self.config.max_orderbook_deviation
        if max_deviation <= 0:
            # No filtering, return best bid
            return Decimal(str(self.orderbook['bids'][0][0]))
            
        min_allowed = reference_price * (Decimal('1') - max_deviation)
        max_allowed = reference_price * (Decimal('1') + max_deviation)
        
        # Find highest bid within range
        for bid in self.orderbook['bids']:
            bid_price = Decimal(str(bid[0]))
            if min_allowed <= bid_price <= max_allowed:
                return bid_price
                
        # If no valid bid found, find the closest one below range
        for bid in self.orderbook['bids']:
            bid_price = Decimal(str(bid[0]))
            if bid_price < min_allowed:
                self.logger.info(f"Using nearest bid below range: {bid_price} (reference: {reference_price})")
                return bid_price
                
        return None
        
    def get_nearest_valid_ask(self, reference_price: Decimal) -> Optional[Decimal]:
        """
        Get the nearest valid ask price within acceptable deviation.
        Returns the lowest ask that's within max_orderbook_deviation of reference price.
        """
        if not hasattr(self, 'orderbook') or not self.orderbook.get('asks'):
            return None
            
        max_deviation = self.config.max_orderbook_deviation
        if max_deviation <= 0:
            # No filtering, return best ask
            return Decimal(str(self.orderbook['asks'][0][0]))
            
        min_allowed = reference_price * (Decimal('1') - max_deviation)
        max_allowed = reference_price * (Decimal('1') + max_deviation)
        
        # Find lowest ask within range
        for ask in self.orderbook['asks']:
            ask_price = Decimal(str(ask[0]))
            if min_allowed <= ask_price <= max_allowed:
                return ask_price
                
        # If no valid ask found, find the closest one above range
        for ask in reversed(self.orderbook['asks']):
            ask_price = Decimal(str(ask[0]))
            if ask_price > max_allowed:
                self.logger.info(f"Using nearest ask above range: {ask_price} (reference: {reference_price})")
                return ask_price
                
        return None


# Updated example usage that loads config directly from YAML
if __name__ == "__main__":
    config = load_config_from_yaml()

    if not config:
        print("\033[91mError: Failed to load configuration from config.yaml\033[0m")
        exit(1)

    print("Configuration loaded successfully:")
    print(f"Exchange: {config.exchange_id}")
    print(f"Symbol: {config.symbol}")

    bot = MarketMakerREST(config)
    print(f"Starting market maker bot for {config.symbol}")

    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("Received keyboard interrupt, shutting down...")
    except Exception as e:
        print(f"Unexpected error: {e}")
        exit(1)
