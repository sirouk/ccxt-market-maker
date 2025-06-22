# Delta-Neutral Market Maker Bot

**Automated liquidity provision for cryptocurrency exchanges** - Set it up in minutes, let it handle the complexity.

## 🎯 What This Does

This bot automatically:
- Places buy & sell orders around the market price
- Maintains balanced inventory between two currencies
- Adjusts to market movements intelligently
- Protects against extreme price outliers

**Perfect for**: Exchange liquidity providers, token projects, and traders earning from spreads.

## 🚀 Quick Start (2 Minutes)

```bash
# 1. Get the code
git clone https://github.com/sirouk/ccxt-market-maker
cd ccxt-market-maker

# 2. Run the manager
./market_maker_manager.sh

# 3. Follow the prompts
# - Choose "Create new instance"
# - Enter your token (e.g., "ATOM")
# - Enter API credentials
# - Use defaults (they're good!)
# - Fund your account
# Done! Your bot is running.
```

That's it! The bot handles all the complexity automatically. 

## 🔧 How It Works (Simply)

1. **You set a token pair** (e.g., ATOM/USDT)
2. **Bot places orders** above and below current price
3. **Orders fill** = You earn the spread
4. **Bot replaces filled orders** automatically
5. **Inventory stays balanced** via smart adjustments

## 📊 What Makes This Smart

### Automatic Features (No Configuration Needed!)

#### 🎯 **Grid Stability**
- Bot only updates orders when price moves significantly
- Reduces unnecessary API calls and fees
- Settings auto-calculate based on your grid spread

#### ⚖️ **Inventory Balance**
- Maintains your target ratio (default 50/50)
- Increases buy orders when low on tokens
- Increases sell orders when high on tokens

#### 🛡️ **Outlier Protection**
- Ignores extreme orders that could hurt you
- Uses market average (VWAP) as reference
- Continues working even if all orders are outliers

#### 🔄 **Smart Recovery**
- Gracefully cancels ALL orders when stopping
- Fetches fresh order data on shutdown (no orphaned orders)
- Tracks order settlement properly
- Restarts from where it left off

#### 💰 **Balance Change Detection** 
- Automatically detects deposits/withdrawals
- Updates orders when you add funds (1% change)
- No need to restart - bot adapts instantly
- Works even when prices are stable

## 💰 Before You Start

### Funding Requirements
You need **both currencies** in your account:
- For ATOM/USDT: Fund with both ATOM and USDT
- Default ratio is 50/50 (configurable)
- Account for ~0.2% trading fees

### Minimum Amounts
- Enough for at least one buy order
- Enough for at least one sell order
- Start small to test!

## 🎛️ Basic Settings

Most users only need to know these:

| Setting | What It Does | Default | 
|---------|--------------|---------|
| `symbol` | Trading pair | ATOM/USDT |
| `grid_levels` | Orders per side | 3 |
| `grid_spread` | Space between orders | 0.05% |
| `min_order_size` | Smallest order | 0.1 |

**Tip**: The defaults work great for most cases!

## 🚦 Using the Manager

The manager script (`./market_maker_manager.sh`) makes everything easy:

### Features
- ✅ **Multiple Bots**: Run different token pairs
- ✅ **Import API Keys**: Reuse credentials from other bots
- ✅ **Live Monitoring**: Check logs and status
- ✅ **Dry Run Mode**: Test without real orders
- ✅ **Easy Reconfigure**: Adjust settings on the fly

### Smart API Key Handling
- ✅ Same key for different tokens (ATOM + LBR)
- ❌ Prevents duplicate bots for same token
- 🔄 Import existing keys with one click

## 🧪 Test First!

Always test your configuration:

```bash
# Run simulation
python tests/simulate_bot_cycle.py configs/YOUR-CONFIG.yaml

# See grid behavior  
python tests/simulate_bot_cycle.py configs/YOUR-CONFIG.yaml --scenarios
```

Shows exactly what the bot would do without risking funds.

## 🎯 Common Scenarios

### Just Starting?
Use defaults! They're designed for safety and effectiveness.

### Low Liquidity Token?
The bot handles thin orderbooks automatically:
- Falls back to safe prices when needed
- Creates liquidity where none exists
- Protects against manipulation

### Volatile Market?
Built-in protections:
- Grid stability prevents thrashing
- Outlier filter ignores extremes  
- Inventory management reduces risk

### Want Conservative Trading?
```yaml
target_inventory_ratio: 0.1  # Hold only 10% in tokens
grid_spread: 0.01           # Wider spreads
```

### Want Aggressive Trading?
```yaml
grid_levels: 10             # More orders
grid_spread: 0.002          # Tighter spreads
```

## 🔍 Advanced Features

<details>
<summary><b>Click to expand advanced configuration</b></summary>

### All Configuration Options

| Parameter | Description | Default |
|-----------|-------------|---------|
| `symbol` | Trading pair | ATOM/USDT |
| `grid_levels` | Number of orders on each side | 3 |
| `grid_spread` | % distance between price levels | 0.0005 |
| `min_order_size` | Minimum order size | 0.1 |
| `max_position` | Maximum position size | 0.5 |
| `target_inventory_ratio` | Target balance ratio | 0.5 |
| `inventory_tolerance` | Acceptable deviation | 0.1 |
| `polling_interval` | Update frequency (seconds) | 8.0 |
| `max_orderbook_deviation` | Outlier filter threshold | 0.1 |
| `outlier_filter_reference` | Price reference source | vwap |
| `out_of_range_pricing_fallback` | Enable fallback pricing | true |
| `out_of_range_price_mode` | Fallback price source | vwap |

### Grid Stability Details

The bot automatically calculates when to update orders:
- **Update Threshold** = `grid_spread ÷ 2`
- **Cooldown Period** = `polling_interval × 3`

This means:
- Tight grids (0.2% spread) → Updates at 0.1% price moves
- Normal grids (1% spread) → Updates at 0.5% price moves
- Wide grids (2% spread) → Updates at 1% price moves

### Outlier Filtering Deep Dive

Reference price options:
- `vwap`: Volume-weighted average (recommended)
- `nearest_bid`: Conservative for selling
- `nearest_ask`: Conservative for buying
- `ticker_mid`: Simple bid/ask midpoint
- `last`: Last traded price

The bot excludes its own orders from calculations to prevent feedback loops.

### Fallback Pricing Modes

When all orders are filtered as outliers:
- `vwap`: Use volume-weighted average
- `nearest_bid`: Use closest bid price
- `nearest_ask`: Use closest ask price
- `auto`: Try multiple sources intelligently

</details>

## 🛠️ Development Setup

<details>
<summary><b>Click to expand development setup</b></summary>

```bash
# Install uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create Python environment
uv venv --python 3.12 --seed
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# Run directly
python -m src.bot.main
```

</details>

## ⚠️ Important Notes

### Risks
- Market prices can move against you
- Technical issues can cause losses
- Start small and monitor closely
- Never invest more than you can afford to lose

### Best Practices
1. **Start Small**: Test with minimal amounts
2. **Monitor Initially**: Watch the first few hours
3. **Check Logs**: Use manager to view activity
4. **Set Limits**: Use `max_position` wisely

## 🆘 Troubleshooting

**Bot not placing orders?**
- Check funding (need both currencies)
- Verify API permissions
- Look for outlier filtering in logs

**Orders at wrong prices?**
- Normal if market has outliers
- Check logs for "filtered" messages
- Adjust `max_orderbook_deviation` if needed

**Container stopped?**
- Use manager option 5 to check logs
- Usually insufficient balance
- Option 2 to restart

## 📝 Quick Reference

### Manager Commands
```bash
./market_maker_manager.sh  # Start manager
# Then choose:
# 1-N) Manage existing bot
# Create new instance
# View logs
# Run simulation
# Reconfigure
```

### Manual Docker Commands
```bash
docker logs ccxt-delta-neutral-atom-1     # View logs
docker restart ccxt-delta-neutral-atom-1  # Restart
docker stop ccxt-delta-neutral-atom-1     # Stop (cancels orders)
```

---

**Remember**: The bot handles the complexity. You just need to fund your account and let it run! 🚀
