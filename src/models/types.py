from typing import Dict, Any, TypedDict, Protocol, runtime_checkable
from decimal import Decimal


class OrderData(TypedDict):
    id: str
    symbol: str
    price: str
    side: str
    amount: str
    filled: str
    status: str
    info: Dict[str, Any]
    created_at: float


class OrderDataWithDisappeared(OrderData):
    """Extended OrderData with disappeared_at timestamp for settlement tracking"""
    disappeared_at: float


class TradeData(TypedDict):
    id: str
    orderId: str
    pair: str
    side: str
    price: float
    quantity: float


class OrderRecord(TypedDict):
    id: str
    pair: str
    side: str
    price: float
    quantity: float


class GridOrder(TypedDict):
    side: str
    price: Decimal
    size: Decimal


@runtime_checkable
class Exchange(Protocol):
    """Protocol for exchange interface"""
    async def fetch_open_orders(self, symbol: str) -> list: ...
    async def fetch_order(self, order_id: str, symbol: str) -> dict: ...
    async def cancel_order(self, order_id: str, symbol: str) -> dict: ...
