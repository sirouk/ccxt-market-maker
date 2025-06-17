from datetime import datetime
import sqlite3
from typing import Union
import os

from _types import OrderRecord, TradeData
from custom_logger import LoggerSetup

class DatabaseManager:
    def __init__(self, db_path: str = "market_maker.db", log_file: str = "market_maker.log"):
        self.logger = LoggerSetup.setup_logger('DatabaseManager', log_file)
        
        # Create directory for database if it doesn't exist
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        """Create necessary tables if they don't exist"""
        cursor = self.conn.cursor()

        # Orders table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            pair TEXT,
            side TEXT,
            price REAL,
            quantity REAL,
            status TEXT,
            timestamp DATETIME,
            filled_quantity REAL DEFAULT 0
        )
        ''')

        # Trades table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            order_id TEXT,
            pair TEXT,
            side TEXT,
            price REAL,
            quantity REAL,
            timestamp DATETIME,
            FOREIGN KEY (order_id) REFERENCES orders(order_id)
        )
        ''')

        self.conn.commit()

    def record_order(self, order_data: Union[OrderRecord, dict]):
        """Record a new order"""
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO orders (order_id, pair, side, price, quantity, status, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            order_data['id'],
            order_data['pair'],
            order_data['side'],
            float(order_data['price']),
            float(order_data['quantity']),
            'ACTIVE',
            datetime.now()
        ))
        self.conn.commit()

    def record_trade(self, trade_data: Union[TradeData, dict]):
        """Record a new trade"""
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO trades (trade_id, order_id, pair, side, price, quantity, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            trade_data['id'],
            trade_data.get('orderId', ''),
            trade_data['pair'],
            trade_data['side'],
            float(trade_data['price']),
            float(trade_data['quantity']),
            datetime.now()
        ))

        # Update the filled quantity in the orders table
        cursor.execute('''
        UPDATE orders
        SET filled_quantity = filled_quantity + ?
        WHERE order_id = ?
        ''', (float(trade_data['quantity']), trade_data.get('orderId', '')))

        self.conn.commit()

    def update_order_status(self, order_id: str, status: str):
        """Update order status"""
        cursor = self.conn.cursor()
        cursor.execute('''
        UPDATE orders
        SET status = ?
        WHERE order_id = ?
        ''', (status, order_id))
        self.conn.commit()
