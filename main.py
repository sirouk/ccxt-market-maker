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
        """Retrieve the current orderbook via REST and store it in memory."""
        try:
            ob = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_order_book,
                "Fetch orderbook",
                self.config.symbol
            )
            # 'bids' and 'asks' are lists of [price, volume]
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
                mid_price = (Decimal(str(best_bid_price)) + Decimal(str(best_ask_price))) / Decimal('2')
                spread = Decimal(str(best_ask_price)) - Decimal(str(best_bid_price))
                spread_pct = (spread / mid_price) * Decimal('100')

                self.logger.debug(f"Orderbook updated - Best bid: {best_bid_price} ({best_bid_volume}), "
                                f"Best ask: {best_ask_price} ({best_ask_volume}), "
                                f"Mid price: {mid_price}, Spread: {spread} ({spread_pct:.4f}%)")
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

            if not self.orderbook['bids'] or not self.orderbook['asks']:
                self.logger.warning("Cannot calculate inventory ratio: orderbook is empty")
                return self.config.target_inventory_ratio

            best_bid_price, _ = self.orderbook['bids'].peekitem(-1)
            best_ask_price, _ = self.orderbook['asks'].peekitem(0)
            mid_price = (Decimal(str(best_bid_price)) + Decimal(str(best_ask_price))) / Decimal('2')

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
            if not self.orderbook['bids'] or not self.orderbook['asks']:
                self.logger.debug("No orderbook data available for grid calculation")
                return []

            best_bid_price, _ = self.orderbook['bids'].peekitem(-1)
            best_ask_price, _ = self.orderbook['asks'].peekitem(0)
            mid_price = (Decimal(str(best_bid_price)) + Decimal(str(best_ask_price))) / Decimal('2')

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
