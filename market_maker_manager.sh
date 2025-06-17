#!/bin/bash

# Colors for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PREFIX="ccxt-delta-neutral"
CONFIG_DIR="./configs"
DATA_DIR="./data"

# Ensure script is run with bash
if [ -z "$BASH_VERSION" ]; then
    echo "Please run this script with bash: bash $0"
    exit 1
fi

# Function to install prerequisites silently
install_prerequisites() {
    # Check if Docker is installed
    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}Docker not found. Installing Docker and dependencies...${NC}"
        
        # Install dependencies
        sudo apt update > /dev/null 2>&1
        sudo apt install -y jq curl nginx-full certbot python3-certbot-nginx > /dev/null 2>&1
        
        # Install Docker
        curl -fsSL https://get.docker.com | bash > /dev/null 2>&1
        
        # Add current user to docker group
        sudo usermod -aG docker $USER
        
        # Install ufw-docker for firewall rules (only on fresh Docker install)
        echo -e "${YELLOW}Setting up UFW-Docker...${NC}"
        sudo wget -q -O /usr/local/bin/ufw-docker https://github.com/chaifeng/ufw-docker/raw/master/ufw-docker
        sudo chmod +x /usr/local/bin/ufw-docker
        sudo ufw-docker install > /dev/null 2>&1
        sudo ufw reload > /dev/null 2>&1
        
        echo -e "${GREEN}Docker and dependencies installed successfully!${NC}"
        echo -e "${YELLOW}Note: You may need to log out and back in for docker group changes to take effect.${NC}"
        
        # Check if user needs to re-login for docker group
        if ! groups | grep -q docker; then
            echo
            echo -e "${YELLOW}Please log out and back in, then run this script again.${NC}"
            exit 0
        fi
    else
        # Check if ufw-docker is installed (in case Docker was already present)
        if command -v ufw &> /dev/null && [ ! -f /usr/local/bin/ufw-docker ]; then
            echo -e "${YELLOW}Installing UFW-Docker for existing Docker installation...${NC}"
            sudo wget -q -O /usr/local/bin/ufw-docker https://github.com/chaifeng/ufw-docker/raw/master/ufw-docker
            sudo chmod +x /usr/local/bin/ufw-docker
            sudo ufw-docker install > /dev/null 2>&1
            sudo ufw reload > /dev/null 2>&1
            echo -e "${GREEN}UFW-Docker installed successfully!${NC}"
        fi
    fi
}

# Function to check if API key is already in use for a coin
check_api_key_usage() {
    local api_key=$1
    local coin=$2
    
    # Check all config files
    for config_file in "$CONFIG_DIR"/*-config.yaml; do
        if [ -f "$config_file" ]; then
            # Extract coin name from filename
            config_coin=$(basename "$config_file" | sed -n 's/\(.*\)-[0-9]*-config.yaml/\1/p')
            
            # Extract API key from config (be careful with special characters)
            config_api_key=$(grep -A1 "^api:" "$config_file" | grep "key:" | sed 's/.*key:.*"\(.*\)".*/\1/')
            
            if [ "$config_api_key" = "$api_key" ] && [ "$config_coin" = "$coin" ]; then
                return 0  # API key already used for this coin
            fi
        fi
    done
    
    return 1  # API key not used for this coin
}

# Function to get existing instances
get_existing_instances() {
    docker ps -a --format "{{.Names}}" | grep "^${PREFIX}-" | sort
}

# Function to display instance details
display_instance_details() {
    local instance=$1
    local status=$(docker ps -a --filter "name=^${instance}$" --format "{{.Status}}")
    local created=$(docker ps -a --filter "name=^${instance}$" --format "{{.CreatedAt}}")
    
    echo -e "${BLUE}Instance:${NC} $instance"
    echo -e "${BLUE}Status:${NC} $status"
    echo -e "${BLUE}Created:${NC} $created"
    
    # Extract coin and instance number from name
    local coin=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
    local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
    
    # Show config file location
    local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
    if [ -f "$config_file" ]; then
        echo -e "${BLUE}Config:${NC} $config_file"
        
        # Show trading pair
        local symbol=$(grep "symbol:" "$config_file" | awk '{print $2}' | tr -d '"')
        echo -e "${BLUE}Trading Pair:${NC} $symbol"
    fi
}

# Function to manage existing instance
manage_instance() {
    local instance=$1
    
    while true; do
        echo
        display_instance_details "$instance"
        echo
        echo "What would you like to do?"
        echo "1) Check logs"
        echo "2) Restart"
        echo "3) Stop"
        echo "4) Delete (including data)"
        echo "5) View configuration"
        echo "6) Back to main menu"
        
        read -p "Enter your choice (1-6): " choice
        
        case $choice in
            1)
                echo -e "\n${BLUE}Recent logs:${NC}"
                docker logs --tail 50 "$instance"
                echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                read
                ;;
            2)
                echo -e "${YELLOW}Restarting instance...${NC}"
                docker restart "$instance"
                echo -e "${GREEN}Instance restarted!${NC}"
                sleep 2
                ;;
            3)
                echo -e "${YELLOW}Stopping instance...${NC}"
                docker stop "$instance"
                echo -e "${GREEN}Instance stopped!${NC}"
                sleep 2
                ;;
            4)
                read -p "Are you sure you want to delete this instance and its data? (yes/no): " confirm
                if [ "$confirm" = "yes" ]; then
                    echo -e "${YELLOW}Deleting instance...${NC}"
                    docker stop "$instance" 2>/dev/null
                    docker rm "$instance"
                    
                    # Extract coin and instance number
                    local coin=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                    local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                    
                    # Remove config and data files
                    rm -f "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                    rm -rf "${DATA_DIR}/${coin}-${instance_num}"
                    
                    echo -e "${GREEN}Instance deleted!${NC}"
                    sleep 2
                    return
                fi
                ;;
            5)
                # Extract coin and instance number
                local coin=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                
                if [ -f "$config_file" ]; then
                    echo -e "\n${BLUE}Configuration for ${instance}:${NC}"
                    echo -e "${YELLOW}(API credentials are partially hidden for security)${NC}\n"
                    
                    # Display config with masked API credentials
                    cat "$config_file" | sed -E 's/(key:.*")(.{4}).*(.{4})(".*)/\1\2****\3\4/; s/(secret:.*")(.{4}).*(.{4})(".*)/\1\2****\3\4/'
                    
                    echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                    read
                else
                    echo -e "${RED}Configuration file not found!${NC}"
                    sleep 2
                fi
                ;;
            6)
                return
                ;;
            *)
                echo -e "${RED}Invalid choice!${NC}"
                ;;
        esac
    done
}

# Function to create new instance
create_new_instance() {
    clear
    echo -e "${GREEN}=== Welcome to Market Maker Bot Setup ===${NC}"
    echo
    echo "This bot helps provide liquidity to cryptocurrency markets by placing"
    echo "buy and sell orders at different price levels (market making)."
    echo
    echo "The bot uses a delta-neutral strategy to maintain a target balance"
    echo "between your base currency (e.g., ATOM) and quote currency (e.g., USDT)."
    echo
    echo -e "${YELLOW}Prerequisites:${NC}"
    echo "1. A LAToken account with API credentials"
    echo "2. Funds in both currencies of your chosen trading pair"
    echo "3. Understanding of the risks involved in automated trading"
    echo
    echo -e "${YELLOW}Press Enter to continue...${NC}"
    read
    
    # Get coin name
    while true; do
        echo
        read -p "Enter the base currency symbol (e.g., ATOM, ETH, BTC): " coin
        coin=$(echo "$coin" | tr '[:lower:]' '[:upper:]')
        
        if [[ ! "$coin" =~ ^[A-Z0-9]+$ ]]; then
            echo -e "${RED}Invalid coin symbol! Please use only letters and numbers.${NC}"
            continue
        fi
        break
    done
    
    # Get trading pair
    echo
    echo "Common quote currencies: USDT, USDC, BTC, ETH"
    read -p "Enter the quote currency (default: USDT): " quote
    quote=${quote:-USDT}
    quote=$(echo "$quote" | tr '[:lower:]' '[:upper:]')
    
    symbol="${coin}/${quote}"
    echo -e "${BLUE}Trading pair: ${symbol}${NC}"
    
    # Get API credentials
    echo
    echo -e "${YELLOW}API Credentials:${NC}"
    echo "You can find these in your LAToken account settings."
    echo "Make sure to enable trading permissions for your API key."
    echo
    
    read -p "Enter your API key: " api_key
    read -s -p "Enter your API secret: " api_secret
    echo
    
    # Check if API key is already used for this coin
    if check_api_key_usage "$api_key" "$coin"; then
        echo -e "\n${RED}Error: This API key is already being used for ${coin}!${NC}"
        echo "Each coin should have a unique API key or use different instances."
        echo -e "${YELLOW}Press Enter to return to main menu...${NC}"
        read
        return
    fi
    
    # Get trading parameters
    echo
    echo -e "${BLUE}=== Trading Parameters ===${NC}"
    echo
    
    read -p "Grid levels (number of orders on each side, default: 3): " grid_levels
    grid_levels=${grid_levels:-3}
    
    read -p "Grid spread (% distance between levels, default: 0.0005 = 0.05%): " grid_spread
    grid_spread=${grid_spread:-0.0005}
    
    read -p "Minimum order size in ${coin} (default: 0.1): " min_order_size
    min_order_size=${min_order_size:-0.1}
    
    read -p "Maximum position in ${coin} (default: 0.5): " max_position
    max_position=${max_position:-0.5}
    
    read -p "Target inventory ratio (0.5 = 50% in each currency, default: 0.5): " target_ratio
    target_ratio=${target_ratio:-0.5}
    
    # Calculate funding requirements
    echo
    echo -e "${BLUE}=== Funding Requirements ===${NC}"
    echo
    echo "Based on your settings, you should fund your account with:"
    echo -e "${GREEN}• ${coin}: $(echo "scale=2; $target_ratio * 100" | bc)% of your trading capital${NC}"
    echo -e "${GREEN}• ${quote}: $(echo "scale=2; (1 - $target_ratio) * 100" | bc)% of your trading capital${NC}"
    echo
    echo "Minimum requirements:"
    echo -e "${YELLOW}• At least ${min_order_size} ${coin} for sell orders${NC}"
    echo -e "${YELLOW}• Equivalent ${quote} for buy orders at current market price${NC}"
    echo
    
    read -p "Have you funded your LAToken account? (yes/no): " funded
    if [ "$funded" != "yes" ]; then
        echo -e "${RED}Please fund your account before proceeding!${NC}"
        echo -e "${YELLOW}Press Enter to return to main menu...${NC}"
        read
        return
    fi
    
    # Find next instance number
    instance_num=1
    while docker ps -a --format "{{.Names}}" | grep -q "^${PREFIX}-${coin}-${instance_num}$"; do
        ((instance_num++))
    done
    
    instance_name="${PREFIX}-${coin}-${instance_num}"
    
    # Create directories
    mkdir -p "$CONFIG_DIR"
    mkdir -p "${DATA_DIR}/${coin}-${instance_num}"
    
    # Create config file
    config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
    cat > "$config_file" << EOF
# Exchange API credentials
api:
  key: "${api_key}"
  secret: "${api_secret}"

# Database and logging
storage:
  db_path: "data/${coin}-${instance_num}/market_maker.db"
  log_file: "data/${coin}-${instance_num}/market_maker.log"

# Bot configuration
bot_config:
  exchange_id: "latoken"
  symbol: "${symbol}"
  grid_levels: ${grid_levels}
  grid_spread: ${grid_spread}
  min_order_size: ${min_order_size}
  max_position: ${max_position}
  polling_interval: 8.0
  target_inventory_ratio: ${target_ratio}
  inventory_tolerance: 0.1
EOF
    
    # Create docker-compose file for this instance
    compose_file="${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
    cat > "$compose_file" << EOF
services:
  ${instance_name}:
    build: .
    container_name: ${instance_name}
    volumes:
      - ${config_file}:/app/config.yaml:ro
      - ${DATA_DIR}/${coin}-${instance_num}:/app/data
    restart: on-failure:3
    tty: true
    stdin_open: true
    stop_grace_period: 60s
EOF
    
    # Start the instance
    echo
    echo -e "${YELLOW}Starting market maker for ${symbol}...${NC}"
    docker compose -f "$compose_file" up -d --build
    
    echo
    echo -e "${GREEN}=== Success! ===${NC}"
    echo
    echo "Your market maker bot is now running!"
    echo -e "${BLUE}Instance name:${NC} ${instance_name}"
    echo -e "${BLUE}Trading pair:${NC} ${symbol}"
    echo -e "${BLUE}Config file:${NC} ${config_file}"
    echo -e "${BLUE}Logs:${NC} ${DATA_DIR}/${coin}-${instance_num}/market_maker.log"
    echo
    echo "The bot will:"
    echo "• Place buy orders below the current market price"
    echo "• Place sell orders above the current market price"
    echo "• Automatically adjust orders as the market moves"
    echo "• Maintain your target inventory ratio"
    echo
    echo "You can check the logs with:"
    echo -e "${YELLOW}docker logs ${instance_name}${NC}"
    echo
    echo -e "${YELLOW}Press Enter to return to main menu...${NC}"
    read
}

# Main menu
main_menu() {
    while true; do
        clear
        echo -e "${GREEN}=== Market Maker Manager ===${NC}"
        echo
        
        # Get existing instances
        instances=($(get_existing_instances))
        
        if [ ${#instances[@]} -gt 0 ]; then
            echo "Existing instances:"
            echo
            for i in "${!instances[@]}"; do
                local status=$(docker ps -a --filter "name=^${instances[$i]}$" --format "{{.Status}}")
                echo "$((i+1))) ${instances[$i]} - $status"
            done
            echo
            echo "$((${#instances[@]}+1))) Create new instance"
            echo "$((${#instances[@]}+2))) Exit"
            
            read -p "Enter your choice: " choice
            
            if [ "$choice" -ge 1 ] && [ "$choice" -le ${#instances[@]} ]; then
                manage_instance "${instances[$((choice-1))]}"
            elif [ "$choice" -eq $((${#instances[@]}+1)) ]; then
                create_new_instance
            elif [ "$choice" -eq $((${#instances[@]}+2)) ]; then
                echo -e "${GREEN}Goodbye!${NC}"
                exit 0
            else
                echo -e "${RED}Invalid choice!${NC}"
                sleep 2
            fi
        else
            echo "No existing instances found."
            echo
            echo "1) Create new instance"
            echo "2) Exit"
            
            read -p "Enter your choice (1-2): " choice
            
            case $choice in
                1)
                    create_new_instance
                    ;;
                2)
                    echo -e "${GREEN}Goodbye!${NC}"
                    exit 0
                    ;;
                *)
                    echo -e "${RED}Invalid choice!${NC}"
                    sleep 2
                    ;;
            esac
        fi
    done
}

# Main execution
echo -e "${BLUE}Checking prerequisites...${NC}"
install_prerequisites

# Create necessary directories
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR"

# Start main menu
main_menu 