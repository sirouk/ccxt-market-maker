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
        """Cancel all locally tracked open orders and wait for confirmation."""
        all_order_ids = list(self.my_orders.keys()) + list(self.recently_closed_orders.keys())

        if not all_order_ids:
            self.logger.info("No orders to cancel")
            return

        self.logger.info(f"Cancelling {len(all_order_ids)} orders...")
        self.logger.debug(f"Orders to cancel: active={len(self.my_orders)}, recently_closed={len(self.recently_closed_orders)}")

        for oid in all_order_ids:
            await self.cancel_order(oid)

        # Wait a bit for cancellations to process
        await asyncio.sleep(2)

        # Verify all orders are cancelled
        await self._verify_cancellations(all_order_ids)

        # Clear local tracking
        self.my_orders.clear()
        self.recently_closed_orders.clear()
        self.logger.debug("All order tracking cleared")

    async def _verify_cancellations(self, cancelled_order_ids: List[str]) -> None:
        """Verify that all orders have been cancelled."""
        max_retries = 5
        for retry in range(max_retries):
            try:
                remaining_orders = await self.retry_handler.retry_with_backoff(
                    self.exchange.fetch_open_orders,
                    "Verify orders cancelled",
                    self.symbol
                )
                our_remaining = [o for o in remaining_orders if str(o['id']) in cancelled_order_ids]

                if not our_remaining:
                    self.logger.info("All orders successfully cancelled")
                    break
                else:
                    self.logger.warning(f"Still have {len(our_remaining)} open orders, retry {retry + 1}/{max_retries}")
                    if retry < max_retries - 1:
                        await asyncio.sleep(2)

            except Exception as e:
                self.logger.error(f"Error checking remaining orders: {e}")
