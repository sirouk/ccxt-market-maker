import sqlite3
import time
from typing import List, Dict, Optional
from src.utils.custom_logger import LoggerSetup
from src.models.types import OrderRecord, TradeData


class DatabaseManager:
    """Manages SQLite database operations for the trading bot."""

    def __init__(self, db_path: str, log_file: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.logger = LoggerSetup.setup_logger('DatabaseManager', log_file)
        self.create_tables()

    def create_tables(self) -> None:
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()

        # Orders table
        cursor.execute('''CREATE TABLE IF NOT EXISTS orders
                        (id TEXT PRIMARY KEY,
                         pair TEXT NOT NULL,
                         side TEXT NOT NULL,
                         price REAL NOT NULL,
                         quantity REAL NOT NULL,
                         timestamp INTEGER NOT NULL,
                         status TEXT DEFAULT 'OPEN')''')

        # Check if status column exists, if not add it (for migration)
        cursor.execute("PRAGMA table_info(orders)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'status' not in columns:
            cursor.execute("ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'OPEN'")
            self.logger.info("Added status column to orders table")

        # Trades table
        cursor.execute('''CREATE TABLE IF NOT EXISTS trades
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         order_id TEXT NOT NULL,
                         pair TEXT NOT NULL,
                         side TEXT NOT NULL,
                         price REAL NOT NULL,
                         quantity REAL NOT NULL,
                         timestamp INTEGER NOT NULL,
                         FOREIGN KEY(order_id) REFERENCES orders(id))''')

        # Performance metrics table
        cursor.execute('''CREATE TABLE IF NOT EXISTS performance
                        (id INTEGER PRIMARY KEY AUTOINCREMENT,
                         timestamp INTEGER NOT NULL,
                         base_balance REAL NOT NULL,
                         quote_balance REAL NOT NULL,
                         total_value_quote REAL NOT NULL,
                         inventory_ratio REAL NOT NULL)''')

        self.conn.commit()

    def record_order(self, order: OrderRecord) -> None:
        """Record a new order in the database."""
        cursor = self.conn.cursor()
        cursor.execute('''INSERT OR REPLACE INTO orders
                         (id, pair, side, price, quantity, timestamp, status)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (order['id'], order['pair'], order['side'], order['price'],
                       order['quantity'], int(time.time()), 'OPEN'))
        self.conn.commit()

    def update_order_status(self, order_id: str, status: str) -> None:
        """Update the status of an existing order."""
        cursor = self.conn.cursor()
        cursor.execute('''UPDATE orders
                         SET status = ?
                         WHERE id = ?''',
                      (status, order_id))
        self.conn.commit()
        self.logger.debug(f"Updated order {order_id} status to {status}")

    def record_trade(self, trade: 'TradeData') -> None:
        """Record a trade execution."""
        cursor = self.conn.cursor()
        cursor.execute('''INSERT INTO trades
                         (order_id, pair, side, price, quantity, timestamp)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (trade['orderId'], trade['pair'], trade['side'], 
                       trade['price'], trade['quantity'], int(time.time())))
        self.conn.commit()

    def record_performance(self, base_balance: float, quote_balance: float,
                          total_value_quote: float, inventory_ratio: float) -> None:
        """Record current performance metrics."""
        cursor = self.conn.cursor()
        cursor.execute('''INSERT INTO performance
                         (timestamp, base_balance, quote_balance,
                          total_value_quote, inventory_ratio)
                         VALUES (?, ?, ?, ?, ?)''',
                      (int(time.time()), base_balance, quote_balance,
                       total_value_quote, inventory_ratio))
        self.conn.commit()

    def get_recent_trades(self, limit: int = 100) -> List[Dict]:
        """Get recent trades from the database."""
        cursor = self.conn.cursor()
        cursor.execute('''SELECT * FROM trades
                         ORDER BY timestamp DESC
                         LIMIT ?''', (limit,))

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_performance_history(self, hours: int = 24) -> List[Dict]:
        """Get performance history for the specified number of hours."""
        cursor = self.conn.cursor()
        since_timestamp = int(time.time()) - (hours * 3600)
        cursor.execute('''SELECT * FROM performance
                         WHERE timestamp > ?
                         ORDER BY timestamp ASC''', (since_timestamp,))

        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()
