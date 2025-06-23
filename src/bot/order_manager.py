import asyncio
import time
from typing import Dict, List, Optional, Set, Any
from ccxt.base.errors import BaseError
from decimal import Decimal
import logging

from src.models.types import OrderData, OrderDataWithDisappeared, TradeData, Exchange
from src.utils.retry_handler import RetryHandler
from src.utils.database_manager import DatabaseManager


class OrderManager:
    """Manages order tracking, settlement, and cancellation."""

    def __init__(
        self,
        exchange: Exchange,
        symbol: str,
        db: DatabaseManager,
        logger: logging.Logger,
        retry_handler: RetryHandler,
        settlement_timeout: int = 60
    ):
        self.exchange = exchange
        self.symbol = symbol
        self.db = db
        self.logger = logger
        self.retry_handler = retry_handler
        self.settlement_timeout = settlement_timeout

        # Track our own orders locally: {order_id -> {...}}
        self.my_orders: Dict[str, OrderData] = {}

        # Track orders that have disappeared from open orders but may still be settling
        self.recently_closed_orders: Dict[str, OrderDataWithDisappeared] = {}

    def _safe_str_to_float(self, value: Any, default: float = 0.0) -> float:
        """Safely convert a value to float, handling 'None' strings and other edge cases."""
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Handle string 'None' or empty strings
            if value.lower() == 'none' or value == '':
                return default
            try:
                return float(value)
            except ValueError:
                self.logger.warning(f"Could not convert '{value}' to float, using default {default}")
                return default
        return default

    def _safe_value_to_str(self, value: Any, default: str = '0') -> str:
        """Safely convert a value to string, handling None and 'None' strings."""
        if value is None:
            return default
        if isinstance(value, str):
            # If it's already string 'None', convert to default
            if value.lower() == 'none':
                return default
            return value
        return str(value)

    async def fetch_open_orders(self) -> None:
        """Fetch and update open orders from exchange."""
        try:
            self.logger.debug(f"Fetching open orders for {self.symbol}")

            open_orders = await self.retry_handler.retry_with_backoff(
                self.exchange.fetch_open_orders,
                "Fetch open orders",
                self.symbol
            )

            self.logger.debug(f"Received {len(open_orders)} open orders from exchange")
            current_ids: Set[str] = set()

            for order in open_orders:
                oid = str(order['id'])
                current_ids.add(oid)

                # Convert numeric fields to string for consistency, handling 'None' strings
                price = self._safe_value_to_str(order.get('price'), '0')
                amount = self._safe_value_to_str(order.get('amount', 0), '0')
                filled = self._safe_value_to_str(order.get('filled', 0), '0')

                # Check if we already have this order in our tracking
                existing_order = self.my_orders.get(oid)

                # Get the created_at timestamp if it exists, otherwise set current time
                created_at = existing_order['created_at'] if existing_order else time.time()

                # Log order details for debugging
                if not existing_order:
                    self.logger.debug(f"New order discovered: {oid} - {order.get('side')} {amount} @ {price}, filled: {filled}")
                elif existing_order.get('filled') != filled:
                    self.logger.debug(f"Order fill update: {oid} - filled changed from {existing_order.get('filled')} to {filled}")

                # Update or create local tracking
                self.my_orders[oid] = OrderData(
                    id=oid,
                    symbol=order['symbol'],
                    price=price,
                    side=order['side'].lower(),
                    amount=amount,
                    filled=filled,
                    status=order.get('status', 'open').upper(),
                    info=order.get('info', {}),
                    created_at=created_at
                )

                # Remove from recently closed if it reappears
                if oid in self.recently_closed_orders:
                    self.logger.info(f"Order {oid} reappeared in open orders, removing from recently closed")
                    del self.recently_closed_orders[oid]

            # Log orders that disappeared
            disappeared_count = len(set(self.my_orders.keys()) - current_ids)
            if disappeared_count > 0:
                self.logger.debug(f"{disappeared_count} orders disappeared from open orders")

            # Handle orders that disappeared from open orders
            await self._handle_disappeared_orders(current_ids)

            self.logger.debug(f"Order tracking updated: {len(self.my_orders)} active orders, "
                            f"{len(self.recently_closed_orders)} recently closed orders")

        except Exception as e:
            self.logger.error(f"Error fetching open orders: {e}")

    async def _handle_disappeared_orders(self, current_ids: Set[str]) -> None:
        """Handle orders that are no longer in open orders."""
        old_ids = set(self.my_orders.keys()) - current_ids
        current_time = time.time()

        for old_id in old_ids:
            old_order = self.my_orders.pop(old_id, None)

            if old_order and old_id not in self.recently_closed_orders:
                self.logger.info(f"Order {old_id} disappeared from open orders, monitoring for settlement")
                # Create extended order data with disappeared_at timestamp
                self.recently_closed_orders[old_id] = OrderDataWithDisappeared(
                    id=old_order['id'],
                    symbol=old_order['symbol'],
                    price=old_order['price'],
                    side=old_order['side'],
                    amount=old_order['amount'],
                    filled=old_order['filled'],
                    status=old_order['status'],
                    info=old_order['info'],
                    created_at=old_order['created_at'],
                    disappeared_at=current_time
                )

        # Process recently closed orders that have passed the settlement timeout
        await self._process_settled_orders(current_time)

    async def _process_settled_orders(self, current_time: float) -> None:
        """Process orders that have passed the settlement timeout."""
        orders_to_finalize: List[str] = []

        for oid, order_data in self.recently_closed_orders.items():
            time_since_disappeared = current_time - order_data['disappeared_at']

            if time_since_disappeared >= self.settlement_timeout:
                orders_to_finalize.append(oid)
                self.logger.debug(f"Order {oid} ready for finalization after {time_since_disappeared:.1f}s settlement period")
            else:
                self.logger.debug(f"Order {oid} still in settlement period ({time_since_disappeared:.1f}s/{self.settlement_timeout}s)")

        # Finalize orders that have passed the timeout
        for oid in orders_to_finalize:
            order_data = self.recently_closed_orders.pop(oid)
            await self._finalize_closed_order(oid, order_data)

    async def _finalize_closed_order(self, order_id: str, order_data: OrderDataWithDisappeared) -> None:
        """Finalize a closed order by checking its final status and recording any trades."""
        try:
            self.logger.info(f"Finalizing closed order {order_id}")

            # Try to fetch the order details one more time to get the final filled amount
            final_filled = 0.0
            try:
                final_order = await self.retry_handler.retry_with_backoff(
                    self.exchange.fetch_order,
                    f"Fetch final order {order_id}",
                    order_id,
                    self.symbol
                )
                final_filled = self._safe_str_to_float(final_order.get('filled', 0))
                self.logger.info(f"Final check for order {order_id}: filled={final_filled}")
            except Exception as e:
                self.logger.warning(f"Could not fetch final order details for {order_id}: {e}")
                # Fall back to the last known filled amount
                final_filled = self._safe_str_to_float(order_data.get('filled', 0))

            # Update database status
            self.db.update_order_status(order_id, 'CLOSED')

            # Record trade if there was any fill
            if final_filled > 0:
                trade_data = TradeData(
                    id=f"trade_{order_id}_{int(time.time())}",
                    orderId=order_id,
                    pair=order_data['symbol'],
                    side=order_data['side'],
                    price=self._safe_str_to_float(order_data['price']),
                    quantity=final_filled
                )
                self.logger.info(f"Recording trade for finalized order {order_id}: {trade_data}")
                self.db.record_trade(trade_data)
                self.logger.info(f"Successfully recorded trade for order {order_id}")
            else:
                self.logger.info(f"Order {order_id} had no fills, skipping trade record")

            self.logger.info(f"Order {order_id} finalized successfully")

        except Exception as e:
            self.logger.error(f"Error finalizing order {order_id}: {e}")

    async def cancel_order(self, order_id: str) -> None:
        """Cancel a single order by ID and update DB."""
        try:
            await self.retry_handler.retry_with_backoff(
                self.exchange.cancel_order,
                f"Cancel order {order_id}",
                order_id,
                self.symbol
            )
            if order_id in self.my_orders:
                self.my_orders.pop(order_id)
            self.db.update_order_status(order_id, 'CANCELLED')
        except Exception as e:
            self.logger.error(f"Error cancelling order {order_id}: {e}")

    async def cancel_all_orders(self) -> None:
        """Cancel all open orders for this symbol, fetching fresh data from exchange."""
        self.logger.info("Starting comprehensive order cancellation...")
        
        try:
            all_open_orders = []
            page = 0
            limit = 100  # Most exchanges support up to 100-500 per page
            
            # Fetch all orders with pagination
            while True:
                self.logger.info(f"Fetching open orders page {page + 1}...")
                try:
                    # Try fetching with pagination parameters
                    open_orders = await self.retry_handler.retry_with_backoff(
                        self.exchange.fetch_open_orders,
                        f"Fetch orders page {page}",
                        self.symbol,
                        None,  # since parameter
                        limit,  # limit parameter
                        {'offset': page * limit}  # Additional params for pagination
                    )
                except TypeError:
                    # If exchange doesn't support those parameters, try without
                    if page == 0:
                        open_orders = await self.retry_handler.retry_with_backoff(
                            self.exchange.fetch_open_orders,
                            "Fetch all orders for cancellation",
                            self.symbol
                        )
                    else:
                        break  # Can't paginate, work with what we have
                
                if not open_orders:
                    break
                    
                all_open_orders.extend(open_orders)
                self.logger.info(f"Found {len(open_orders)} orders on page {page + 1}")
                
                # If we got less than limit, we've reached the end
                if len(open_orders) < limit:
                    break
                    
                page += 1
                
                # Safety check to prevent infinite loops
                if page > 10:  # Max 1000 orders
                    self.logger.warning("Reached pagination limit, proceeding with orders found")
                    break
            
            if not all_open_orders:
                self.logger.info("No open orders found to cancel")
                return
            
            self.logger.info(f"Found total of {len(all_open_orders)} open orders to cancel")
            
            # Cancel in batches to avoid rate limits
            batch_size = 10
            cancelled_count = 0
            failed_count = 0
            
            for i in range(0, len(all_open_orders), batch_size):
                batch = all_open_orders[i:i + batch_size]
                self.logger.info(f"Cancelling batch {i//batch_size + 1} ({len(batch)} orders)...")
                
                # Cancel orders in parallel within batch
                cancel_tasks = []
                for order in batch:
                    order_id = str(order['id'])
                    cancel_tasks.append(self._cancel_single_order(order_id))
                
                # Wait for batch to complete
                results = await asyncio.gather(*cancel_tasks, return_exceptions=True)
                
                # Count results
                for result in results:
                    if isinstance(result, Exception):
                        failed_count += 1
                    else:
                        cancelled_count += 1
                
                # Small delay between batches to respect rate limits
                if i + batch_size < len(all_open_orders):
                    await asyncio.sleep(0.5)
            
            self.logger.info(f"Initial cancellation complete: {cancelled_count} cancelled, {failed_count} failed")
            
            # Wait for cancellations to process
            await asyncio.sleep(3)
            
            # Verify all orders are cancelled (with pagination again)
            remaining_count = 0
            verification_attempts = 0
            max_verification_attempts = 3
            
            while verification_attempts < max_verification_attempts:
                remaining_orders = []
                page = 0
                
                # Fetch remaining orders with pagination
                while True:
                    try:
                        orders = await self.retry_handler.retry_with_backoff(
                            self.exchange.fetch_open_orders,
                            f"Verify orders cancelled page {page}",
                            self.symbol,
                            None,
                            limit,
                            {'offset': page * limit}
                        )
                    except TypeError:
                        if page == 0:
                            orders = await self.retry_handler.retry_with_backoff(
                                self.exchange.fetch_open_orders,
                                "Verify all orders cancelled",
                                self.symbol
                            )
                        else:
                            break
                    
                    if not orders:
                        break
                        
                    remaining_orders.extend(orders)
                    
                    if len(orders) < limit:
                        break
                        
                    page += 1
                    if page > 10:
                        break
                
                remaining_count = len(remaining_orders)
                
                if remaining_count == 0:
                    self.logger.info("✓ All orders successfully cancelled!")
                    break
                
                self.logger.warning(f"WARNING: {remaining_count} orders still open after cancellation!")
                verification_attempts += 1
                
                if verification_attempts < max_verification_attempts:
                    self.logger.info(f"Retrying cancellation for {remaining_count} stubborn orders (attempt {verification_attempts}/{max_verification_attempts})...")
                    
                    # Cancel remaining orders more aggressively
                    for order in remaining_orders:
                        order_id = str(order['id'])
                        try:
                            # Direct cancel without retry wrapper for speed
                            await self.exchange.cancel_order(order_id, self.symbol)
                            self.logger.debug(f"Force cancelled order {order_id}")
                        except Exception as e:
                            self.logger.error(f"Failed to force cancel order {order_id}: {e}")
                    
                    # Wait before next verification
                    await asyncio.sleep(2)
            
            if remaining_count > 0:
                self.logger.error(f"ERROR: Failed to cancel {remaining_count} orders after {max_verification_attempts} attempts!")
                self.logger.error("Manual intervention may be required to cancel remaining orders.")
            
            # Clear local tracking
            self.my_orders.clear()
            self.recently_closed_orders.clear()
            self.logger.debug("Order tracking cleared")
            
        except Exception as e:
            self.logger.error(f"Critical error during order cancellation: {e}")
            self.logger.error("IMPORTANT: Some orders may still be open! Check exchange manually.")
            # Still try to clear local tracking
            self.my_orders.clear()
            self.recently_closed_orders.clear()
    
    async def _cancel_single_order(self, order_id: str) -> None:
        """Cancel a single order and update database."""
        try:
            await self.retry_handler.retry_with_backoff(
                self.exchange.cancel_order,
                f"Cancel order {order_id}",
                order_id,
                self.symbol
            )
            self.logger.debug(f"Cancelled order {order_id}")
            
            # Update database
            self.db.update_order_status(order_id, 'CANCELLED')
            
        except Exception as e:
            self.logger.error(f"Failed to cancel order {order_id}: {e}")
            raise  # Re-raise for proper error counting
