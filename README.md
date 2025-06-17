# Market Maker Bot

This is a market-making trading bot for cryptocurrency exchanges, using CCXT.

## 🚀 Quick Start (Recommended for Beginners)

The easiest way to get started is using our interactive setup script:

```bash
./market_maker_manager.sh
```

This script will:
- ✅ Automatically install Docker and other prerequisites
- ✅ Guide you through setting up your first market maker instance
- ✅ Help you manage multiple trading pairs
- ✅ Provide easy access to logs and instance management
- ✅ Prevent duplicate API keys for the same coin

### What the Script Does

1. **First Run**: Checks and installs prerequisites (Docker, docker-compose, ufw-docker)
2. **Main Menu**: Shows all your running market maker instances
3. **New Instance Setup**: 
   - Explains what market making is
   - Asks for your trading pair (e.g., ATOM/USDT)
   - Collects your LAToken API credentials
   - Helps you configure trading parameters
   - Calculates funding requirements
   - Starts your market maker bot
4. **Instance Management**:
   - Check logs
   - Restart instances
   - Stop instances
   - Delete instances (with confirmation)

### Quick Example

1. Run the script: `./market_maker_manager.sh`
2. Choose "Create new instance"
3. Enter your coin (e.g., "ATOM")
4. Enter your API credentials from LAToken
5. Use default settings or customize
6. Fund your account as instructed
7. Your bot starts running!

Each instance runs in its own Docker container with the naming pattern:
`ccxt-delta-neutral-[COIN]-[NUMBER]`

---

## Prerequisites

### Account Funding

**IMPORTANT**: Before running the bot, you must fund your exchange account with both currencies of the trading pair.

For optimal performance, fund your account with a balance that matches your `target_inventory_ratio` setting:

**Example for ATOM/USDT with 50% target inventory ratio:**
- Deposit 50% of your trading capital in ATOM
- Deposit 50% of your trading capital in USDT

**Example for ETH/USDT with 30% target inventory ratio:**
- Deposit 30% of your trading capital in ETH
- Deposit 70% of your trading capital in USDT

**Why this matters:**
- The bot needs both currencies to place buy and sell orders
- Starting with the target ratio minimizes initial rebalancing
- Insufficient funds in either currency will prevent order placement
- The bot will automatically adjust order sizes based on available balances

**Minimum Requirements:**
- Enough base currency (e.g., ATOM) to place at least one sell order of `min_order_size`
- Enough quote currency (e.g., USDT) to place at least one buy order of `min_order_size` at current market prices
- Consider trading fees (typically 0.1-0.2%) when calculating minimum balances

## Quick Start

1. **Fund your exchange account** (see Prerequisites above)

2. Configure your settings in `config.yaml`
   - Fill in your API credentials
   - Adjust trading parameters as needed

3. Create a data directory for persistence
   ```
   mkdir -p data
   ```

4. Run with docker-compose (one-liner):
   ```
   docker-compose up --build
   ```

5. To run in the background:
   ```
   docker-compose up -d --build
   ```

6. To stop:
   ```
   docker-compose down
   ```

## Configuration

Edit `config.yaml` to customize:
- API credentials
- Trading parameters:
  - Trading pair (symbol)
  - Grid levels and spread
  - Order sizes
  - Polling interval
  - Inventory management settings

## Parameter Glossary

| Parameter | Description |
|-----------|-------------|
| `grid_levels` | Number of price levels on each side of the mid-price. Higher values create more orders with wider price coverage. |
| `grid_spread` | Percentage distance between each grid level. For example, 0.001 means each level is 0.1% away from the next level. |
| `min_order_size` | Minimum order size in base currency (e.g., ETH in ETH/USDT). Orders smaller than this won't be placed. |
| `max_position` | Maximum total position size in base currency. Limits the bot's exposure to price movements. |
| `polling_interval` | How often (in seconds) the bot checks prices and adjusts orders. Lower values make the bot more responsive but may hit API rate limits. |
| `target_inventory_ratio` | Desired ratio of base currency value to total portfolio value. 0.5 means aiming for 50% in base currency, 50% in quote currency. |
| `inventory_tolerance` | Acceptable deviation from target inventory ratio before the bot starts adjusting order sizes. E.g., 0.1 with target of 0.5 means acceptable range is 0.4-0.6. |

### Inventory Management

The bot uses `target_inventory_ratio` and `inventory_tolerance` to maintain a balanced portfolio:

- When your inventory has too much base currency (e.g., ETH):
  - Sell orders are increased in size
  - Buy orders are reduced in size

- When your inventory has too little base currency:
  - Buy orders are increased in size
  - Sell orders are reduced in size

This helps to naturally rebalance your portfolio through trading activity.

## Logs and Data

Logs and database are stored in the `./data` directory which is mounted as a volume in the container.
