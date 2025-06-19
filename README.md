# Delta-Neutral Market Maker Bot

A market-making bot that provides liquidity on centralized exchanges (CEXs) using a delta-neutral strategy to improve order book depth and user experience.

## What This Does

This bot:
- **Provides Liquidity**: Places buy and sell orders around the market price to improve order book depth
- **Delta-Neutral Strategy**: Maintains a balanced inventory between base and quote currencies to minimize directional risk
- **Improves Trading UX**: Creates tighter spreads and better liquidity for traders on the exchange
- **Automated Rebalancing**: Adjusts order sizes to maintain your target inventory ratio
- **Smart Order Filtering**: Ignores outlier orders that could distort market pricing

Perfect for:
- Exchange partners providing liquidity
- Traders wanting to earn from spreads while minimizing directional exposure
- Projects looking to improve liquidity for their tokens

## 🚀 Quick Start (Recommended)

The easiest way to get started is using our interactive setup script:

```bash
./market_maker_manager.sh
```

This script will:
- ✅ Automatically install Docker and prerequisites
- ✅ Guide you through setting up your market maker
- ✅ Help manage multiple trading pairs
- ✅ Import API credentials from existing setups (no need to re-enter)
- ✅ Smart API key management (same key works for different tokens, prevents duplicates)
- ✅ Graceful shutdown (automatically cancels all orders when stopping)
- ✅ Handle all the technical details for you

### Quick Example

1. Run: `./market_maker_manager.sh`
2. Choose "Create new instance"
3. Enter your coin (e.g., "ATOM")
4. Enter your LAToken API credentials (or import from existing setup)
5. Use default settings or customize
6. Fund your account as instructed
7. Your bot starts providing liquidity!

**API Key Usage:**
- ✅ **Same API key for different tokens**: ATOM/USDT + LBR/USDT (allowed)
- ❌ **Same API key for same token twice**: ATOM-1 + ATOM-2 (prevented)
- 💡 **Multiple trading pairs**: One API key can run many different token pairs

### Manager Features

The management script now includes a **simulation feature** for all instances:

1. Select any instance (running, stopped, or orphaned)
2. Choose option 6: **"Run simulation (dry run)"**
3. See exactly what the bot would do without placing real orders

This is perfect for:
- Testing configurations before going live
- Diagnosing issues with stopped containers
- Understanding the bot's pricing logic
- Verifying your balance is sufficient
- Checking if outlier filtering is working correctly

---

## Prerequisites

### Account Funding

**IMPORTANT**: You must fund your exchange account with both currencies before starting.

The bot maintains a target ratio between currencies (default 50/50):
- **ATOM/USDT with 50% ratio**: Fund with 50% ATOM, 50% USDT value
- **ETH/USDT with 30% ratio**: Fund with 30% ETH, 70% USDT value

**Minimum Requirements:**
- Enough base currency for at least one sell order
- Enough quote currency for at least one buy order
- Account for trading fees (typically 0.1-0.2%)

**Safety Features:**
- 🛡️ **Graceful Shutdown**: When you stop the bot, it automatically cancels all open orders before shutting down
- 🔄 **Smart Recovery**: Can restart from where it left off if temporarily stopped

## Configuration

Key parameters in `config.yaml`:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `symbol` | Trading pair (e.g., ATOM/USDT) | ATOM/USDT |
| `grid_levels` | Number of orders on each side | 3 |
| `grid_spread` | % distance between price levels | 0.0005 (0.05%) |
| `min_order_size` | Minimum order size | 0.1 |
| `max_position` | Maximum position size | 0.5 |
| `target_inventory_ratio` | Target balance ratio (0.5 = 50/50) | 0.5 |
| `inventory_tolerance` | Acceptable deviation from target | 0.1 |
| `polling_interval` | Update frequency (seconds) | 8.0 |
| `max_orderbook_deviation` | Filter orders beyond % from reference price | 0.1 (10%) |
| `out_of_range_pricing_fallback` | Enable fallback pricing when all orders filtered | true |
| `out_of_range_price_mode` | Fallback price source when all orders out of range | vwap |

### How It Works

1. **Grid Trading**: Places orders at multiple price levels above and below market price
2. **Inventory Management**: Automatically adjusts order sizes to maintain target ratio
3. **Continuous Rebalancing**: As orders fill, new ones are placed to maintain liquidity
4. **Outlier Protection**: Filters out extreme orders that could distort pricing

Example with default settings:
- Places 3 buy orders below market price
- Places 3 sell orders above market price
- Each level is 0.05% apart
- Adjusts sizes to maintain 50/50 inventory balance
- Ignores orders more than 10% from last traded price

## Advanced Features

### Outlier Filtering
The bot includes intelligent outlier filtering to handle exchanges with extreme orders (e.g., LAToken):

- **max_orderbook_deviation**: Filters out orders that deviate more than X% from the reference price
- **Reference Price Hierarchy**:
  1. **VWAP (Volume Weighted Average Price)**: Most reliable, based on actual trading volume
  2. **Ticker Bid/Ask Mid-Price**: Used if spread is reasonable (<10x)
  3. **Last Traded Price**: Least reliable, only used as last resort

Example configuration:
```yaml
max_orderbook_deviation: 0.1  # Filter orders >10% from VWAP/reference price
```

**Why VWAP?** VWAP is calculated based on actual trading volume, making it much more resistant to outlier trades and market manipulation compared to simple last traded price.

### Out-of-Range Price Fallback

**This feature only applies when:**
1. `max_orderbook_deviation` > 0 (outlier filtering is enabled)
2. ALL bids and asks fall outside the configured tolerance
3. `out_of_range_pricing_fallback` is set to true

When all orders are filtered out as outliers, the bot needs a fallback price source to continue market making. Without this fallback, the bot would stop placing orders entirely.

```yaml
out_of_range_pricing_fallback: true  # Enable fallback when all orders filtered
out_of_range_price_mode: vwap  # Options: 'vwap', 'nearest_bid', 'nearest_ask', 'auto'
```

#### Available Fallback Modes:

1. **VWAP Mode** (default, safest):
   - Uses Volume Weighted Average Price from the exchange
   - Best for most situations, especially volatile markets
   - Represents actual trading activity

2. **Nearest Bid Mode**:
   - Uses the nearest valid bid price (even if outside tolerance)
   - Conservative for buying (may get better fills)
   - Good when you trust bid prices more than asks

3. **Nearest Ask Mode**:
   - Uses the nearest valid ask price (even if outside tolerance)
   - Conservative for selling (may get better fills)
   - Good when you trust ask prices more than bids

4. **Auto Mode** (adaptive fallback):
   - Tries multiple price sources in order:
     1. VWAP (if available)
     2. Ticker bid/ask mid-price (if spread < 10x)
     3. Last traded price
   - Most flexible, adapts to available data
   - Good for markets with intermittent data

**Example**: On LAToken with extreme outliers:
- Normal orderbook mid-price would be: 0.005 (from outlier asks)
- VWAP: 0.00005153 (actual trading average)
- Nearest Bid: 0.00001 (80% below VWAP)
- Nearest Ask: 0.005 (9700% above VWAP!)

Using `out_of_range_price_mode: vwap` protects you from these extremes.

### Directional Bias for Out-of-Range Pricing

When `out_of_range_pricing_fallback` is enabled and all orders are filtered out, the bot can also use **directional bias** to create synthetic orders that help with inventory rebalancing:

- **Too much base currency**: Creates synthetic bid slightly below fallback price (encourages selling)
- **Too little base currency**: Creates synthetic ask slightly above fallback price (encourages buying)
- **Within tolerance**: Uses symmetric pricing around fallback price

This ensures the bot continues to work toward your target inventory ratio even when the entire orderbook is unusable.

**How it works:**
1. All real orderbook orders are filtered out as outliers
2. Bot calculates a fallback price using your configured `out_of_range_price_mode`
3. Bot checks your current inventory ratio
4. Places synthetic orders at prices that favor rebalancing
5. Helps maintain your target ratio even in extreme market conditions

### Inventory Management

The bot automatically adjusts order sizes to maintain your target inventory ratio:

- **Target Ratio**: Set `target_inventory_ratio` (0.5 = 50/50 balance)
- **Tolerance**: Set `inventory_tolerance` for acceptable deviation
- **Dynamic Adjustment**: Increases buy orders when low on base currency, increases sell orders when high

Example:
- Current: 60% ATOM, 40% USDT
- Target: 50% ATOM, 50% USDT
- Result: Bot increases sell order sizes and decreases buy order sizes

### Bot Cycle Simulation

Use the `simulate_bot_cycle.py` script to see exactly what the bot would do in one market-making cycle:

```bash
# Simulate with default config
python3 simulate_bot_cycle.py

# Simulate with specific config
python3 simulate_bot_cycle.py configs/YOUR-CONFIG.yaml
```

The simulation shows:
1. **Market Data Fetching**: Current ticker, orderbook, and balances
2. **Outlier Filtering**: Which orders are filtered and why
3. **Price Calculation**: How the mid-price is determined
4. **Inventory Analysis**: Current vs target ratios
5. **Order Grid Generation**: All orders that would be created
6. **Execution Plan**: Which orders can actually be placed with current balances

This is useful for:
- Testing configurations before running the bot
- Understanding the bot's decision-making process
- Debugging issues with order placement
- Verifying outlier filtering is working correctly

### Low Liquidity Markets

For markets with thin orderbooks or many outlier orders:

1. **Automatic Fallback**: If filtering removes all bids or asks, the bot automatically uses the last traded price
2. **Grid Generation**: Still creates full buy/sell grids even with incomplete orderbooks
3. **Price Discovery**: Helps establish proper price levels in illiquid markets

### Conservative Inventory Management

For tokens where you want minimal exposure:

```yaml
target_inventory_ratio: 0.01    # Hold only 1% in base currency
inventory_tolerance: 0.005      # Very tight tolerance
```

This is useful for:
- High volatility tokens
- Initial market making with limited capital
- Testing new markets

### Aggressive Market Making

For deeper liquidity provision:

```yaml
grid_levels: 20                 # 20 orders each side
grid_spread: 0.002              # 0.2% between levels
max_position: 10000000          # Large position limit
```

## Manual Setup

If you prefer manual setup over the script:

```bash
# 1. Configure
cp config.yaml.example config.yaml
# Edit config.yaml with your API credentials

# 2. Build and run
docker compose up -d --build

# 3. Check logs
docker logs -f market-maker-0l

# 4. Stop
docker compose down
```

## Important Notes

- **Network Mode**: Uses Docker host network for reliability
- **Data Storage**: Logs and database stored in `./data/`
- **Multiple Instances**: Each coin pair runs in its own container
- **Risk Management**: Always start with small amounts to test
- **Order Precision**: The bot handles extreme price precision automatically

## ⚠️ Risks

- **Market Risk**: Prices can move against your inventory
- **Technical Risk**: Software/network issues can cause losses
- **Exchange Risk**: API issues or exchange problems
- **Liquidity Risk**: May be difficult to exit positions in thin markets

**Best Practices:**
- Start small and monitor closely
- Set appropriate `max_position` limits
- Understand exchange fee structure
- Use outlier filtering for markets with extreme orders
- Never invest more than you can afford to lose

## Troubleshooting

**Common Issues:**

- **"Permission denied"**: Run `chmod +x market_maker_manager.sh`
- **"Cannot connect"**: Check API credentials and permissions
- **"Insufficient balance"**: Ensure you funded both currencies
- **Container stopped**: Check logs with management script
- **Wrong price levels**: Check if outlier filtering is needed

**View Logs:**
```bash
docker logs ccxt-delta-neutral-[coin]-[number]
```

**Check for Outlier Orders:**
If the bot places orders at unexpected prices, check logs for:
```
Orderbook filtered: X bids, Y asks (removed Z outlier bids, W outlier asks)
```

This indicates outlier filtering is working correctly.

## License

This software is provided as-is. Use at your own risk. Always test with small amounts first.
