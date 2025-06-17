# Delta-Neutral Market Maker Bot

A market-making bot that provides liquidity on centralized exchanges (CEXs) using a delta-neutral strategy to improve order book depth and user experience.

## What This Does

This bot:
- **Provides Liquidity**: Places buy and sell orders around the market price to improve order book depth
- **Delta-Neutral Strategy**: Maintains a balanced inventory between base and quote currencies to minimize directional risk
- **Improves Trading UX**: Creates tighter spreads and better liquidity for traders on the exchange
- **Automated Rebalancing**: Adjusts order sizes to maintain your target inventory ratio

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
- ✅ Handle all the technical details for you

### Quick Example

1. Run: `./market_maker_manager.sh`
2. Choose "Create new instance"
3. Enter your coin (e.g., "ATOM")
4. Enter your LAToken API credentials
5. Use default settings or customize
6. Fund your account as instructed
7. Your bot starts providing liquidity!

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

### How It Works

1. **Grid Trading**: Places orders at multiple price levels above and below market price
2. **Inventory Management**: Automatically adjusts order sizes to maintain target ratio
3. **Continuous Rebalancing**: As orders fill, new ones are placed to maintain liquidity

Example with default settings:
- Places 3 buy orders below market price
- Places 3 sell orders above market price
- Each level is 0.05% apart
- Adjusts sizes to maintain 50/50 inventory balance

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

## ⚠️ Risks

- **Market Risk**: Prices can move against your inventory
- **Technical Risk**: Software/network issues can cause losses
- **Exchange Risk**: API issues or exchange problems
- **Liquidity Risk**: May be difficult to exit positions in thin markets

**Best Practices:**
- Start small and monitor closely
- Set appropriate `max_position` limits
- Understand exchange fee structure
- Never invest more than you can afford to lose

## Troubleshooting

**Common Issues:**

- **"Permission denied"**: Run `chmod +x market_maker_manager.sh`
- **"Cannot connect"**: Check API credentials and permissions
- **"Insufficient balance"**: Ensure you funded both currencies
- **Container stopped**: Check logs with management script

**View Logs:**
```bash
docker logs ccxt-delta-neutral-[coin]-[number]
```

## License

This software is provided as-is. Use at your own risk. Always test with small amounts first.
