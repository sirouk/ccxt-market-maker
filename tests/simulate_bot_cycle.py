#!/usr/bin/env python3
"""
Market Maker Bot Cycle Simulator

This script simulates one complete market making cycle without placing real orders.
It shows exactly what the bot would do: fetching market data, calculating mid-price,
checking inventory, and generating the order grid.

Usage:
    python simulate_bot_cycle.py [config_file]

If no config file is specified, it will use the default config.yaml
"""

import sys
import os
from datetime import datetime
from decimal import Decimal
import yaml
import time

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt
from sortedcontainers import SortedDict

from src.models.config import Config
from src.utils.retry_handler import RetryHandler
from src.utils.custom_logger import LoggerSetup

class BotCycleSimulator:
    """Simulates one complete market-making bot cycle"""

    def __init__(self, config_path='configs/LBR-1-config.yaml'):
        """Initialize with configuration"""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.bot_config = self.config['bot_config']

        # Initialize exchange
        self.exchange = ccxt.latoken({
            'enableRateLimit': True,
            'apiKey': self.config['api']['key'],
            'secret': self.config['api']['secret']
        })

        # Store market data
        self.ticker = None
        self.orderbook = None
        self.balance = None
        self.filtered_bids = SortedDict()
        self.filtered_asks = SortedDict()
        
        # Grid stability tracking
        self.grid_anchor_price = None
        self.last_grid_update_time = None

    def run_cycle(self, simulate_price_movement=False):
        """Run one complete bot cycle"""
        print(f"=== MARKET MAKER BOT SIMULATION ===")
        print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Config: {self.bot_config['symbol']} on {self.bot_config['exchange_id']}")
        print(f"Out-of-Range Price Mode: {self.bot_config.get('out_of_range_price_mode', 'vwap')}\n")

        # Step 1: Fetch market data
        self._fetch_market_data()
        if not self.ticker or not self.orderbook or not self.balance:
            print("\n❌ CYCLE ABORTED: Failed to fetch market data")
            return

        # Step 2: Apply outlier filtering
        self._filter_orderbook()

        # Step 3: Calculate mid-price
        mid_price = self._calculate_mid_price()
        if not mid_price:
            print("\n❌ CYCLE ABORTED: Could not determine mid-price")
            return

        # Step 3.5: Check grid stability
        should_update = self._check_grid_stability(mid_price)
        
        if should_update:
            # Step 4: Calculate inventory
            inventory_ratio, needs_rebalancing = self._calculate_inventory(mid_price)

            # Step 5: Generate order grid
            buy_orders, sell_orders = self._generate_order_grid(mid_price, inventory_ratio)

            # Step 6: Show what would be executed
            self._show_execution_plan(buy_orders, sell_orders)
            
            # Update grid anchor
            self.grid_anchor_price = mid_price
            self.last_grid_update_time = time.time()
        else:
            print("\n=== GRID STABLE - NO UPDATES NEEDED ===")
            print("Orders would remain in their current positions")
            
        # Optional: Simulate price movement
        if simulate_price_movement:
            self._simulate_price_scenarios()

    def _fetch_market_data(self):
        """Step 1: Fetch all market data"""
        print("=== STEP 1: FETCHING MARKET DATA ===\n")

        try:
            # Fetch ticker
            self.ticker = self.exchange.fetch_ticker(self.bot_config['symbol'])
            print("Ticker Data:")
            print(f"  VWAP: {self.ticker.get('vwap', 'N/A')}")
            print(f"  Last: {self.ticker.get('last', 'N/A')}")
            print(f"  Bid: {self.ticker.get('bid', 'N/A')}")
            print(f"  Ask: {self.ticker.get('ask', 'N/A')}")
            print(f"  24h Volume: {self.ticker.get('baseVolume', 'N/A'):,.0f} LBR")

            # Fetch orderbook
            self.orderbook = self.exchange.fetch_order_book(self.bot_config['symbol'], limit=20)
            print(f"\nOrderbook Depth:")
            print(f"  Bids: {len(self.orderbook['bids'])} levels")
            print(f"  Asks: {len(self.orderbook['asks'])} levels")

            # Fetch balance
            self.balance = self.exchange.fetch_balance()
            lbr_free = self.balance.get('LBR', {}).get('free', 0)
            usdt_free = self.balance.get('USDT', {}).get('free', 0)
            print(f"\nAccount Balances:")
            print(f"  LBR: {lbr_free:,.0f}")
            print(f"  USDT: {usdt_free:,.2f}")
        except Exception as e:
            print(f"Error fetching market data: {e}")

    def _filter_orderbook(self):
        """Step 2: Filter orderbook for outliers"""
        print("\n=== STEP 2: OUTLIER FILTERING ===\n")
        
        if not self.ticker or not self.orderbook:
            print("❌ No market data available for filtering")
            return

        # Get reference price (VWAP preferred)
        vwap = Decimal(str(self.ticker.get('vwap', 0)))
        last_price = Decimal(str(self.ticker.get('last', 0)))
        reference_price = vwap if vwap > 0 else last_price

        print(f"Reference Price: {reference_price:.8f} (using {'VWAP' if vwap > 0 else 'last price'})")

        # Calculate allowed range
        max_deviation = Decimal(str(self.bot_config['max_orderbook_deviation']))
        min_allowed = reference_price * (Decimal('1') - max_deviation)
        max_allowed = reference_price * (Decimal('1') + max_deviation)

        print(f"Max Deviation: ±{float(max_deviation)*100}%")
        print(f"Allowed Range: {min_allowed:.8f} - {max_allowed:.8f}")

        # Filter bids
        print(f"\nFiltering Bids:")
        original_bid_count = len(self.orderbook.get('bids', []))
        for bid in self.orderbook.get('bids', []):
            price = Decimal(str(bid[0]))
            if min_allowed <= price <= max_allowed:
                self.filtered_bids[price] = Decimal(str(bid[1]))
            else:
                deviation = ((price - reference_price) / reference_price) * 100
                print(f"  ❌ Filtered: {price:.8f} ({deviation:+.1f}% from reference)")

        print(f"  Result: {original_bid_count} → {len(self.filtered_bids)} bids")

        # Filter asks
        print(f"\nFiltering Asks:")
        original_ask_count = len(self.orderbook.get('asks', []))
        for ask in self.orderbook.get('asks', []):
            price = Decimal(str(ask[0]))
            if min_allowed <= price <= max_allowed:
                self.filtered_asks[price] = Decimal(str(ask[1]))
            else:
                deviation = ((price - reference_price) / reference_price) * 100
                print(f"  ❌ Filtered: {price:.8f} ({deviation:+.1f}% from reference)")

        print(f"  Result: {original_ask_count} → {len(self.filtered_asks)} asks")

    def _calculate_mid_price(self):
        """Step 3: Calculate mid-price using price hierarchy"""
        print("\n=== STEP 3: CALCULATING MID-PRICE ===\n")
        
        if not self.ticker:
            print("❌ No ticker data available")
            return None

        # Option 1: Filtered orderbook
        if self.filtered_bids and self.filtered_asks:
            best_bid_item = self.filtered_bids.peekitem(-1)
            best_ask_item = self.filtered_asks.peekitem(0)
            best_bid = best_bid_item[0]
            best_ask = best_ask_item[0]
            mid_price = (best_bid + best_ask) / Decimal('2')
            spread = best_ask - best_bid
            spread_pct = (spread / mid_price) * 100

            print(f"✅ Using filtered orderbook:")
            print(f"   Best Bid: {best_bid:.8f}")
            print(f"   Best Ask: {best_ask:.8f}")
            print(f"   Mid-Price: {mid_price:.8f}")
            print(f"   Spread: {spread_pct:.2f}%")
            return mid_price

        # Orderbook is filtered empty, check price mode
        price_mode = self.bot_config.get('out_of_range_price_mode', 'vwap')
        print(f"⚠️  Orderbook filtered empty")
        print(f"   Using out-of-range price mode: {price_mode}")

        # Get VWAP as reference
        vwap = Decimal(str(self.ticker.get('vwap', 0)))

        if price_mode == 'nearest_bid' and self.orderbook and self.orderbook.get('bids'):
            # Find nearest bid
            best_bid = Decimal(str(self.orderbook['bids'][0][0]))
            print(f"✅ Using nearest bid: {best_bid:.8f}")
            if vwap > 0:
                print(f"   ({(best_bid/vwap - 1)*100:+.1f}% from VWAP)")
            return best_bid

        elif price_mode == 'nearest_ask' and self.orderbook and self.orderbook.get('asks'):
            # Find nearest ask
            best_ask = Decimal(str(self.orderbook['asks'][0][0]))
            print(f"✅ Using nearest ask: {best_ask:.8f}")
            if vwap > 0:
                print(f"   ({(best_ask/vwap - 1)*100:+.1f}% from VWAP)")
            return best_ask

        elif price_mode == 'auto':
            # Auto mode - try all sources
            print("   Auto mode - trying all price sources...")

            # Try VWAP first
            if vwap > 0:
                print(f"✅ Auto: Using VWAP: {vwap:.8f}")
                return vwap

            # Try ticker bid/ask
            ticker_bid = Decimal(str(self.ticker.get('bid', 0)))
            ticker_ask = Decimal(str(self.ticker.get('ask', 0)))
            if ticker_bid > 0 and ticker_ask > 0:
                spread_ratio = ticker_ask / ticker_bid
                if spread_ratio < Decimal('10'):
                    mid_price = (ticker_bid + ticker_ask) / Decimal('2')
                    print(f"✅ Auto: Using ticker mid-price: {mid_price:.8f}")
                    return mid_price
                else:
                    print(f"   Auto: Ticker spread too wide ({spread_ratio:.1f}x)")

            # Use last price as final option
            last_price = Decimal(str(self.ticker.get('last', 0)))
            if last_price > 0:
                print(f"⚠️  Auto: Using last price: {last_price:.8f}")
                return last_price

        # Default: Use VWAP
        if vwap > 0:
            print(f"✅ Using VWAP: {vwap:.8f}")
            return vwap

        # Fallback options...
        ticker_bid = Decimal(str(self.ticker.get('bid', 0)))
        ticker_ask = Decimal(str(self.ticker.get('ask', 0)))
        if ticker_bid > 0 and ticker_ask > 0:
            spread_ratio = ticker_ask / ticker_bid
            if spread_ratio < Decimal('10'):
                mid_price = (ticker_bid + ticker_ask) / Decimal('2')
                print(f"✅ Using ticker bid/ask mid-price: {mid_price:.8f}")
                print(f"   Bid: {ticker_bid:.8f}, Ask: {ticker_ask:.8f}")
                return mid_price
            else:
                print(f"❌ Ticker spread too wide (ratio: {spread_ratio:.1f}x)")

        # Last resort: Last price
        last_price = Decimal(str(self.ticker.get('last', 0)))
        if last_price > 0:
            print(f"⚠️  Using last price (final fallback): {last_price:.8f}")
            return last_price

        print("❌ No valid price source available!")
        return None

    def _check_grid_stability(self, current_mid_price):
        """Step 3.5: Check if grid needs updating based on price movement"""
        print("\n=== STEP 3.5: GRID STABILITY CHECK ===\n")
        
        # Calculate dynamic threshold based on grid spread
        grid_spread = Decimal(str(self.bot_config['grid_spread']))
        dynamic_threshold = grid_spread * Decimal('0.5')
        
        # Calculate dynamic cooldown (3x polling interval)
        polling_interval = float(self.bot_config.get('polling_interval', 5))
        dynamic_cooldown = polling_interval * 3
        
        print(f"Grid Stability Parameters:")
        print(f"  Update Threshold: {float(dynamic_threshold)*100:.2f}% (grid_spread/2)")
        print(f"  Cooldown Period: {dynamic_cooldown:.1f}s (3 × polling_interval)")
        
        # First grid or no anchor
        if self.grid_anchor_price is None:
            print("\n✅ Grid update required: Initial grid creation")
            return True
            
        # Check cooldown
        if self.last_grid_update_time is not None:
            time_since_update = time.time() - self.last_grid_update_time
            if time_since_update < dynamic_cooldown:
                print(f"\n⏳ Grid update cooldown active: {time_since_update:.1f}s / {dynamic_cooldown:.1f}s")
                print(f"   Must wait {dynamic_cooldown - time_since_update:.1f}s more")
                return False
        
        # Check price movement
        price_change = abs(current_mid_price - self.grid_anchor_price) / self.grid_anchor_price
        
        print(f"\nPrice Movement Analysis:")
        print(f"  Anchor Price: {self.grid_anchor_price:.8f}")
        print(f"  Current Price: {current_mid_price:.8f}")
        print(f"  Change: {float(price_change)*100:.2f}%")
        print(f"  Threshold: {float(dynamic_threshold)*100:.2f}%")
        
        if price_change >= dynamic_threshold:
            print(f"\n✅ Grid update triggered: Price moved {float(price_change)*100:.2f}%")
            return True
        else:
            print(f"\n❌ Grid stable: Price change below threshold")
            print("\n🎯 GRID MANAGEMENT:")
            print("  Only orders outside acceptable range would be cancelled:")
            # Calculate furthest grid positions
            grid_levels = self.bot_config['grid_levels']
            furthest_spread = grid_spread * grid_levels
            min_buy_range = current_mid_price * (Decimal('1') - furthest_spread) * Decimal('0.9')
            max_sell_range = current_mid_price * (Decimal('1') + furthest_spread) * Decimal('1.1')
            print(f"  - Buy orders below {min_buy_range:.8f} (10% buffer beyond furthest grid)")
            print(f"  - Sell orders above {max_sell_range:.8f} (10% buffer beyond furthest grid)")
            print(f"  - Any orders on wrong side of mid price ({current_mid_price:.8f})")
            return False

    def _calculate_inventory(self, mid_price):
        """Step 4: Calculate inventory ratio and rebalancing needs"""
        print("\n=== STEP 4: INVENTORY ANALYSIS ===\n")
        
        if not self.balance:
            print("❌ No balance data available")
            return Decimal('0.5'), 'balanced'

        lbr_balance = Decimal(str(self.balance.get('LBR', {}).get('free', 0)))
        usdt_balance = Decimal(str(self.balance.get('USDT', {}).get('free', 0)))

        # Calculate portfolio value
        lbr_value = lbr_balance * mid_price
        total_value = lbr_value + usdt_balance

        print(f"Portfolio Valuation:")
        print(f"  LBR: {lbr_balance:,.0f} × {mid_price:.8f} = {lbr_value:.2f} USDT")
        print(f"  USDT: {usdt_balance:.2f}")
        print(f"  Total: {total_value:.2f} USDT")

        if total_value == 0:
            print("\n⚠️  Empty portfolio!")
            return Decimal('0.5'), 'balanced'

        # Calculate ratios
        current_ratio = lbr_value / total_value
        target_ratio = Decimal(str(self.bot_config['target_inventory_ratio']))
        tolerance = Decimal(str(self.bot_config['inventory_tolerance']))

        print(f"\nInventory Ratios:")
        print(f"  Current: {current_ratio:.4f} ({float(current_ratio)*100:.2f}% LBR)")
        print(f"  Target: {target_ratio:.4f} ({float(target_ratio)*100:.2f}% LBR)")
        print(f"  Tolerance: ±{float(tolerance)*100:.1f}%")

        # Determine rebalancing needs
        if current_ratio < target_ratio - tolerance:
            status = 'need_more_base'
            print(f"\n📈 Status: NEED MORE LBR (below target range)")
        elif current_ratio > target_ratio + tolerance:
            status = 'too_much_base'
            print(f"\n📉 Status: TOO MUCH LBR (above target range)")
        else:
            status = 'balanced'
            print(f"\n✅ Status: BALANCED (within target range)")

        return current_ratio, status

    def _generate_order_grid(self, mid_price, inventory_ratio):
        """Step 5: Generate the order grid"""
        print("\n=== STEP 5: GENERATING ORDER GRID ===\n")

        grid_spread = Decimal(str(self.bot_config['grid_spread']))
        min_order_size = Decimal(str(self.bot_config['min_order_size']))
        grid_levels = self.bot_config['grid_levels']

        print(f"Grid Configuration:")
        print(f"  Levels: {grid_levels} each side")
        print(f"  Spread: {float(grid_spread)*100}% between levels")
        print(f"  Base order size: {min_order_size:,.0f} LBR")

        # Generate orders
        buy_orders = []
        sell_orders = []

        print(f"\nGenerating BUY orders:")
        for i in range(grid_levels):
            level = i + 1
            spread_multiplier = grid_spread * level
            price = mid_price * (Decimal('1') - spread_multiplier)

            # Linear size increase
            size = min_order_size * level

            # Apply inventory adjustment
            if inventory_ratio < self.bot_config['target_inventory_ratio']:
                # Need more base, increase buy sizes
                size = size * Decimal('1.2')

            buy_orders.append((price, size))
            print(f"  Level {level}: {size:,.0f} LBR @ {price:.8f}")

        print(f"\nGenerating SELL orders:")
        for i in range(grid_levels):
            level = i + 1
            spread_multiplier = grid_spread * level
            price = mid_price * (Decimal('1') + spread_multiplier)

            # Linear size increase
            size = min_order_size * level

            # Apply inventory adjustment
            if inventory_ratio > self.bot_config['target_inventory_ratio']:
                # Too much base, increase sell sizes
                size = size * Decimal('1.2')

            sell_orders.append((price, size))
            print(f"  Level {level}: {size:,.0f} LBR @ {price:.8f}")

        return buy_orders, sell_orders

    def _show_execution_plan(self, buy_orders, sell_orders):
        """Step 6: Show what orders would be placed"""
        print("\n=== STEP 6: EXECUTION PLAN ===\n")
        
        if not self.balance:
            print("❌ No balance data available")
            return

        lbr_balance = Decimal(str(self.balance.get('LBR', {}).get('free', 0)))
        usdt_balance = Decimal(str(self.balance.get('USDT', {}).get('free', 0)))

        # Check buy orders
        print("BUY Orders to Place:")
        total_buy_cost = Decimal('0')
        placeable_buys = 0

        for i, (price, size) in enumerate(buy_orders):
            cost = price * size * Decimal('1.002')  # Include fee
            if total_buy_cost + cost <= usdt_balance:
                print(f"  ✅ Level {i+1}: {size:,.0f} LBR @ {price:.8f} (cost: {cost:.2f} USDT)")
                total_buy_cost += cost
                placeable_buys += 1
            else:
                print(f"  ❌ Level {i+1}: Insufficient USDT (need {cost:.2f})")

        print(f"\nTotal buy orders: {placeable_buys}/{len(buy_orders)}")
        print(f"Total USDT needed: {total_buy_cost:.2f} / {usdt_balance:.2f} available")

        # Check sell orders
        print("\n\nSELL Orders to Place:")
        total_sell_size = Decimal('0')
        placeable_sells = 0

        for i, (price, size) in enumerate(sell_orders):
            if total_sell_size + size <= lbr_balance:
                print(f"  ✅ Level {i+1}: {size:,.0f} LBR @ {price:.8f}")
                total_sell_size += size
                placeable_sells += 1
            else:
                print(f"  ❌ Level {i+1}: Insufficient LBR (need {size:,.0f})")

        print(f"\nTotal sell orders: {placeable_sells}/{len(sell_orders)}")
        print(f"Total LBR needed: {total_sell_size:,.0f} / {lbr_balance:,.0f} available")

        # Summary
        print("\n=== CYCLE SUMMARY ===")
        print(f"✅ Would place {placeable_buys} buy orders")
        print(f"✅ Would place {placeable_sells} sell orders")
        print(f"📊 Total orders: {placeable_buys + placeable_sells}")

        if placeable_buys == 0 and placeable_sells == 0:
            print("\n⚠️  WARNING: No orders would be placed! Check your balances.")
            
    def _simulate_price_scenarios(self):
        """Simulate different price scenarios to show grid stability behavior"""
        print("\n\n=== GRID STABILITY SCENARIOS ===\n")
        
        if not self.grid_anchor_price:
            print("❌ No anchor price set")
            return
            
        grid_spread = Decimal(str(self.bot_config['grid_spread']))
        threshold = grid_spread * Decimal('0.5')
        
        scenarios = [
            ("Small movement (0.2%)", Decimal('0.002')),
            ("Near threshold (0.4%)", Decimal('0.004')),
            ("Above threshold (0.6%)", Decimal('0.006')),
            ("Large movement (1.5%)", Decimal('0.015'))
        ]
        
        print(f"Current anchor price: {self.grid_anchor_price:.8f}")
        print(f"Update threshold: {float(threshold)*100:.2f}%\n")
        
        for name, change in scenarios:
            new_price = self.grid_anchor_price * (Decimal('1') + change)
            would_update = change >= threshold
            print(f"{name}:")
            print(f"  New price: {new_price:.8f}")
            print(f"  Grid update: {'✅ YES' if would_update else '❌ NO'}")
            print()

def main():
    """Main entry point"""
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'configs/LBR-1-config.yaml'

    simulator = BotCycleSimulator(config_path)
    
    # Run main cycle
    simulator.run_cycle()
    
    # Optional: Show price scenarios
    if len(sys.argv) > 2 and sys.argv[2] == '--scenarios':
        simulator._simulate_price_scenarios()

if __name__ == "__main__":
    main()