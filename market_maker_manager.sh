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
    # Check if bc is installed (needed for calculations)
    if ! command -v bc &> /dev/null; then
        echo -e "${YELLOW}Installing bc calculator...${NC}"
        if command -v apt-get &> /dev/null; then
            sudo apt-get update > /dev/null 2>&1
            sudo apt-get install -y bc > /dev/null 2>&1
        elif command -v yum &> /dev/null; then
            sudo yum install -y bc > /dev/null 2>&1
        elif command -v brew &> /dev/null; then
            brew install bc > /dev/null 2>&1
        fi
        echo -e "${GREEN}bc installed successfully!${NC}"
    fi
    
    # Check if Docker is installed
    if ! command -v docker &> /dev/null; then
        echo -e "${YELLOW}Docker not found. Installing Docker...${NC}"
        
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
        
        echo -e "${GREEN}Docker installed successfully!${NC}"
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

# Function to check if API key is already in use for a specific coin
check_api_key_usage() {
    local api_key=$1
    local coin=$2
    
    # Check all config files
    for config_file in "$CONFIG_DIR"/*-config.yaml; do
        if [ -f "$config_file" ]; then
            # Extract coin name from filename
            config_coin=$(basename "$config_file" | sed -n 's/\(.*\)-[0-9]*-config.yaml/\1/p')
            
            # Extract API key from config (be careful with special characters)
            config_api_key=$(grep -A2 "^api:" "$config_file" | grep "key:" | sed 's/.*key:.*"\(.*\)".*/\1/')
            
            # Only return true if SAME API key is used for SAME coin
            if [ "$config_api_key" = "$api_key" ] && [ "$config_coin" = "$coin" ]; then
                return 0  # API key already used for this specific coin
            fi
        fi
    done
    
    return 1  # API key not used for this specific coin (different coins are OK)
}

# Function to get existing instances
get_existing_instances() {
    # Docker containers use lowercase names
    docker ps -a --format "{{.Names}}" | grep -E "^${PREFIX}-[a-z0-9]+-[0-9]+$" | sort
}

# Function to detect orphaned data/configs
detect_orphaned_data() {
    local orphaned=()
    
    # Check config files
    for config_file in "$CONFIG_DIR"/*-config.yaml; do
        if [ -f "$config_file" ]; then
            # Extract coin and instance number from filename
            local basename=$(basename "$config_file")
            local coin=$(echo "$basename" | sed -n 's/\(.*\)-[0-9]*-config.yaml/\1/p')
            local instance_num=$(echo "$basename" | sed -n 's/.*-\([0-9]*\)-config.yaml/\1/p')
            
            # Check if corresponding container exists (with lowercase)
            local expected_container="${PREFIX}-$(echo $coin | tr '[:upper:]' '[:lower:]')-${instance_num}"
            
            if ! docker ps -a --format "{{.Names}}" | grep -q "^${expected_container}$"; then
                orphaned+=("${coin}-${instance_num}")
            fi
        fi
    done
    
    echo "${orphaned[@]}"
}

# Function to display instance details
display_instance_details() {
    local instance=$1
    local status=$(docker ps -a --filter "name=^${instance}$" --format "{{.Status}}")
    local created=$(docker ps -a --filter "name=^${instance}$" --format "{{.CreatedAt}}")
    
    echo -e "${BLUE}Instance:${NC} $instance"
    echo -e "${BLUE}Status:${NC} $status"
    echo -e "${BLUE}Created:${NC} $created"
    
    # Extract coin and instance number from name (coin is lowercase in Docker)
    local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
    local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
    local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
    
    # Show config file location (uses uppercase coin name)
    local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
    if [ -f "$config_file" ]; then
        echo -e "${BLUE}Config:${NC} $config_file"
        
        # Show trading pair
        local symbol=$(grep "symbol:" "$config_file" | awk '{print $2}' | tr -d '"')
        echo -e "${BLUE}Trading Pair:${NC} $symbol"
        
        # Show outlier filtering setting
        local max_deviation=$(grep "max_orderbook_deviation:" "$config_file" | awk '{print $2}')
        if [ -n "$max_deviation" ] && [ "$max_deviation" != "0" ]; then
            local deviation_pct=$(echo "scale=1; $max_deviation * 100" | bc)
            echo -e "${BLUE}Outlier Filter:${NC} ±${deviation_pct}% from last price"
        else
            echo -e "${BLUE}Outlier Filter:${NC} Disabled"
        fi
    fi
}

# Function to manage existing instance
manage_instance() {
    local instance=$1
    
    # Check if container is stopped or has issues
    local container_status=$(docker inspect -f '{{.State.Status}}' "$instance" 2>/dev/null)
    local exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$instance" 2>/dev/null)
    
    # If container is not running, offer recovery options
    if [ "$container_status" != "running" ]; then
        echo
        echo -e "${YELLOW}Container ${instance} is not running!${NC}"
        echo -e "${BLUE}Status:${NC} $container_status"
        if [ -n "$exit_code" ] && [ "$exit_code" != "0" ]; then
            echo -e "${RED}Exit code:${NC} $exit_code"
        fi
        echo
        echo "What would you like to do?"
        echo "1) Try to restart with existing data"
        echo "2) Start fresh (wipe data, keep config)"
        echo "3) Remove container only (keep data and config)"
        echo "4) Remove everything (container, data, and config)"
        echo "5) View recent logs to diagnose issue"
        echo "6) Run simulation (dry run)"
        echo "7) Reconfigure"
        echo "8) Back to main menu"
        
        read -p "Enter your choice (1-8): " recovery_choice
        
        case $recovery_choice in
            1)
                echo -e "${YELLOW}Attempting to restart container...${NC}"
                docker start "$instance"
                if [ $? -eq 0 ]; then
                    echo -e "${GREEN}Container restarted successfully!${NC}"
                else
                    echo -e "${RED}Failed to restart. Check logs for details.${NC}"
                fi
                sleep 2
                ;;
            2)
                # Extract coin and instance number
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                
                echo -e "${YELLOW}Removing old container and data...${NC}"
                echo -e "${BLUE}Gracefully stopping container (cancelling orders)...${NC}"
                docker stop "$instance" 2>/dev/null
                docker rm "$instance" 2>/dev/null
                
                # Wipe data directory
                rm -rf "${DATA_DIR}/${coin}-${instance_num}"
                mkdir -p "${DATA_DIR}/${coin}-${instance_num}"
                
                # Rebuild and start
                local compose_file="${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
                if [ -f "$compose_file" ]; then
                    echo -e "${YELLOW}Rebuilding and starting fresh...${NC}"
                    docker compose -f "$compose_file" up -d --build
                    if [ $? -eq 0 ]; then
                        echo -e "${GREEN}Container started fresh successfully!${NC}"
                    else
                        echo -e "${RED}Failed to start.${NC}"
                    fi
                else
                    echo -e "${RED}Compose file not found!${NC}"
                fi
                sleep 3
                ;;
            3)
                echo -e "${YELLOW}Removing container only...${NC}"
                docker rm "$instance"
                echo -e "${GREEN}Container removed. Data and config preserved.${NC}"
                echo -e "${BLUE}To recreate, use 'Create new instance' with the same coin.${NC}"
                sleep 3
                return
                ;;
            4)
                # Extract coin and instance number
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                
                read -p "Are you sure you want to remove EVERYTHING? (yes/no): " confirm
                if [ "$confirm" = "yes" ]; then
                    echo -e "${YELLOW}Removing container, data, and config...${NC}"
                    docker rm "$instance" 2>/dev/null
                    rm -rf "${DATA_DIR}/${coin}-${instance_num}"
                    rm -f "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                    rm -f "${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
                    echo -e "${GREEN}Everything removed!${NC}"
                    sleep 2
                    return
                fi
                ;;
            5)
                echo -e "\n${BLUE}Recent logs:${NC}"
                docker logs --tail 100 "$instance"
                echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                read
                manage_instance "$instance"  # Go back to recovery menu
                return
                ;;
            6)
                # Extract coin and instance number
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                
                if [ -f "$config_file" ]; then
                    echo -e "\n${BLUE}Running market simulation for ${instance}...${NC}"
                    echo -e "${YELLOW}This is a dry run - no actual orders will be placed.${NC}"
                    echo -e "${YELLOW}The simulation shows what the bot would do in one market cycle.${NC}\n"
                    
                    # Check if scripts/simulate_bot_cycle.py exists
                    if [ -f "scripts/simulate_bot_cycle.py" ]; then
                        # Run the simulation
                        python3 scripts/simulate_bot_cycle.py "$config_file"
                        
                        echo -e "\n${GREEN}Simulation complete!${NC}"
                        echo -e "${BLUE}This can help diagnose configuration issues.${NC}"
                        echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                        read
                    else
                        echo -e "${RED}Error: scripts/simulate_bot_cycle.py not found!${NC}"
                        echo -e "${YELLOW}Make sure you're running this from the project directory.${NC}"
                        echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                        read
                    fi
                else
                    echo -e "${RED}Configuration file not found!${NC}"
                    sleep 2
                fi
                manage_instance "$instance"  # Go back to recovery menu
                return
                ;;
            7)
                # Extract coin and instance number
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                
                if [ -f "$config_file" ]; then
                    echo -e "\n${BLUE}Reconfiguring ${instance}...${NC}"
                    reconfigure_instance "$config_file" "$coin" "$instance_num"
                else
                    echo -e "${RED}Configuration file not found!${NC}"
                    sleep 2
                fi
                manage_instance "$instance"  # Go back to recovery menu
                return
                ;;
            8)
                return
                ;;
            *)
                echo -e "${RED}Invalid choice!${NC}"
                sleep 1
                manage_instance "$instance"  # Go back to recovery menu
                return
                ;;
        esac
        
        # After recovery action, show the regular menu
        manage_instance "$instance"
        return
    fi
    
    # Regular menu for running containers
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
        echo "6) Run simulation (dry run)"
        echo "7) Reconfigure"
        echo "8) Back to main menu"
        
        read -p "Enter your choice (1-8): " choice
        
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
                echo -e "${BLUE}The bot will gracefully cancel all open orders before shutting down.${NC}"
                echo -e "${BLUE}This may take up to 60 seconds...${NC}"
                docker stop "$instance"
                echo -e "${GREEN}Instance stopped gracefully!${NC}"
                echo -e "${GREEN}All orders have been cancelled.${NC}"
                sleep 2
                ;;
            4)
                read -p "Are you sure you want to delete this instance and its data? (yes/no): " confirm
                if [ "$confirm" = "yes" ]; then
                    echo -e "${YELLOW}Deleting instance...${NC}"
                    echo -e "${BLUE}Gracefully stopping container (cancelling orders)...${NC}"
                    docker stop "$instance" 2>/dev/null
                    docker rm "$instance"
                    
                    # Extract coin and instance number (coin is lowercase in Docker)
                    local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                    local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
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
                # Extract coin and instance number (coin is lowercase in Docker)
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
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
                # Extract coin and instance number (coin is lowercase in Docker)
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                
                if [ -f "$config_file" ]; then
                    echo -e "\n${BLUE}Running market simulation for ${instance}...${NC}"
                    echo -e "${YELLOW}This is a dry run - no actual orders will be placed.${NC}"
                    echo -e "${YELLOW}The simulation shows what the bot would do in one market cycle.${NC}\n"
                    
                    # Check if scripts/simulate_bot_cycle.py exists
                    if [ -f "scripts/simulate_bot_cycle.py" ]; then
                        # Run the simulation
                        python3 scripts/simulate_bot_cycle.py "$config_file"
                        
                        echo -e "\n${GREEN}Simulation complete!${NC}"
                        echo -e "${BLUE}This shows exactly what orders the bot would place with current market conditions.${NC}"
                        echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                        read
                    else
                        echo -e "${RED}Error: scripts/simulate_bot_cycle.py not found!${NC}"
                        echo -e "${YELLOW}Make sure you're running this from the project directory.${NC}"
                        echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                        read
                    fi
                else
                    echo -e "${RED}Configuration file not found!${NC}"
                    sleep 2
                fi
                ;;
            7)
                # Extract coin and instance number (coin is lowercase in Docker)
                local coin_lower=$(echo "$instance" | sed "s/^${PREFIX}-\(.*\)-[0-9]*$/\1/")
                local coin=$(echo "$coin_lower" | tr '[:lower:]' '[:upper:]')
                local instance_num=$(echo "$instance" | sed "s/^${PREFIX}-.*-\([0-9]*\)$/\1/")
                local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                
                if [ -f "$config_file" ]; then
                    echo -e "\n${BLUE}Reconfiguring ${instance}...${NC}"
                    reconfigure_instance "$config_file" "$coin" "$instance_num"
                else
                    echo -e "${RED}Configuration file not found!${NC}"
                    sleep 2
                fi
                ;;
            8)
                return
                ;;
            *)
                echo -e "${RED}Invalid choice!${NC}"
                ;;
        esac
    done
}

# Function to get existing API keys from all config files
get_existing_api_keys() {
    local api_keys=()
    
    # Scan all config files
    for config_file in "$CONFIG_DIR"/*-config.yaml; do
        if [ -f "$config_file" ]; then
            # Extract coin and instance number from filename
            local basename=$(basename "$config_file")
            local coin=$(echo "$basename" | sed -n 's/\(.*\)-[0-9]*-config.yaml/\1/p')
            local instance_num=$(echo "$basename" | sed -n 's/.*-\([0-9]*\)-config.yaml/\1/p')
            
            # Extract API key, secret, and symbol from config
            local api_key=$(grep -A2 "^api:" "$config_file" | grep "key:" | sed 's/.*key:.*"\(.*\)".*/\1/')
            local api_secret=$(grep -A2 "^api:" "$config_file" | grep "secret:" | sed 's/.*secret:.*"\(.*\)".*/\1/')
            local symbol=$(grep "symbol:" "$config_file" | awk '{print $2}' | tr -d '"')
            
            if [ -n "$api_key" ] && [ -n "$api_secret" ]; then
                # Store as: coin|instance_num|api_key|api_secret|symbol
                api_keys+=("${coin}|${instance_num}|${api_key}|${api_secret}|${symbol}")
            fi
        fi
    done
    
    echo "${api_keys[@]}"
}

# Function to import API credentials from existing configs
import_api_credentials() {
    local new_coin=$1
    local existing_keys=($(get_existing_api_keys))
    
    if [ ${#existing_keys[@]} -eq 0 ]; then
        return 1  # No existing keys found
    fi
    
    echo >&2
    echo -e "${BLUE}=== Import API Credentials ===${NC}" >&2
    echo -e "${YELLOW}Found existing API key pairs from other configurations:${NC}" >&2
    echo >&2
    
    # Display available API keys with partial masking
    for i in "${!existing_keys[@]}"; do
        IFS='|' read -r coin instance_num api_key api_secret symbol <<< "${existing_keys[$i]}"
        
        # Check if this API key is already used for the new coin
        local key_available=true
        if check_api_key_usage "$api_key" "$new_coin"; then
            key_available=false
        fi
        
        # Mask API key and secret for display
        local masked_key="${api_key:0:4}****${api_key: -4}"
        local masked_secret="${api_secret:0:4}****${api_secret: -4}"
        
        if [ "$key_available" = true ]; then
            echo -e "$((i+1))) ${GREEN}${coin}-${instance_num}${NC} (${symbol})" >&2
            echo -e "    API Key: ${masked_key}" >&2
            echo -e "    Secret:  ${masked_secret}" >&2
        else
            echo -e "$((i+1))) ${RED}${coin}-${instance_num}${NC} (${symbol}) ${RED}[Already used for ${new_coin}]${NC}" >&2
            echo -e "    API Key: ${masked_key}" >&2
            echo -e "    Secret:  ${masked_secret}" >&2
        fi
        echo >&2
    done
    
    echo "$((${#existing_keys[@]}+1))) Enter new API credentials manually" >&2
    echo >&2
    
    while true; do
        read -p "Select API credentials to import (1-$((${#existing_keys[@]}+1))): " choice
        
        if [ "$choice" -eq $((${#existing_keys[@]}+1)) ]; then
            return 1  # User chose to enter manually
        elif [ "$choice" -ge 1 ] && [ "$choice" -le ${#existing_keys[@]} ]; then
            local selected_index=$((choice-1))
            IFS='|' read -r coin instance_num selected_api_key selected_api_secret symbol <<< "${existing_keys[$selected_index]}"
            
                         # Check if this API key can be used for the new coin
             if check_api_key_usage "$selected_api_key" "$new_coin"; then
                 echo -e "${RED}Error: This API key is already being used for another ${new_coin} instance!${NC}" >&2
                 echo "Please select a different API key or enter new credentials." >&2
                 echo >&2
                 continue
             fi
            
            # Confirm selection
            echo -e "\n${YELLOW}You selected API credentials from: ${coin}-${instance_num} (${symbol})${NC}" >&2
            read -p "Confirm this selection? (yes/no): " confirm
            
                         if [ "$confirm" = "yes" ]; then
                 # Return the selected credentials (format: key|secret)
                 echo "${selected_api_key}|${selected_api_secret}"
                 return 0  # Success
             fi
        else
            echo -e "${RED}Invalid choice! Please select 1-$((${#existing_keys[@]}+1))${NC}" >&2
        fi
    done
}

# Function to reconfigure existing instance
reconfigure_instance() {
    local config_file=$1
    local coin=$2
    local instance_num=$3
    
    echo
    echo -e "${BLUE}=== Reconfigure ${coin}-${instance_num} ===${NC}"
    echo -e "${YELLOW}Press Enter to keep current values (shown in brackets)${NC}"
    echo
    
    # Read current values from config file
    local current_symbol=$(grep "symbol:" "$config_file" | awk '{print $2}' | tr -d '"')
    local current_grid_levels=$(grep "grid_levels:" "$config_file" | awk '{print $2}')
    local current_grid_spread=$(grep "grid_spread:" "$config_file" | awk '{print $2}')
    local current_min_order_size=$(grep "min_order_size:" "$config_file" | awk '{print $2}')
    local current_max_position=$(grep "max_position:" "$config_file" | awk '{print $2}')
    local current_target_ratio=$(grep "target_inventory_ratio:" "$config_file" | awk '{print $2}')
    local current_inventory_tolerance=$(grep "inventory_tolerance:" "$config_file" | awk '{print $2}')
    local current_max_deviation=$(grep "max_orderbook_deviation:" "$config_file" | awk '{print $2}')
    local current_filter_ref=$(grep "outlier_filter_reference:" "$config_file" | awk '{print $2}')
    local current_pricing_fallback=$(grep "out_of_range_pricing_fallback:" "$config_file" | awk '{print $2}')
    local current_price_mode=$(grep "out_of_range_price_mode:" "$config_file" | awk '{print $2}')
    local current_polling_interval=$(grep "polling_interval:" "$config_file" | awk '{print $2}')
    
    # Extract quote currency from symbol
    local quote=$(echo "$current_symbol" | cut -d'/' -f2)
    
    # Get trading parameters with current values as defaults
    echo -e "${BLUE}=== Trading Parameters ===${NC}"
    echo -e "${BLUE}Current symbol: ${current_symbol}${NC}"
    echo
    
    echo -e "${YELLOW}Number of buy/sell orders to place on each side of the market${NC}"
    echo -e "${YELLOW}Current value: ${current_grid_levels}${NC}"
    read -p "Grid levels (1-20 recommended) [${current_grid_levels}]: " grid_levels
    grid_levels=${grid_levels:-$current_grid_levels}
    
    echo
    echo -e "${YELLOW}Distance between each order level as a decimal (0.001 = 0.1%)${NC}"
    echo -e "${YELLOW}Current value: ${current_grid_spread}${NC}"
    read -p "Grid spread (0.0005-0.01 typical) [${current_grid_spread}]: " grid_spread
    grid_spread=${grid_spread:-$current_grid_spread}
    
    echo
    echo -e "${YELLOW}Smallest order size the exchange allows for ${coin}${NC}"
    echo -e "${YELLOW}Current value: ${current_min_order_size}${NC}"
    read -p "Minimum order size [${current_min_order_size}]: " min_order_size
    min_order_size=${min_order_size:-$current_min_order_size}
    
    echo
    echo -e "${YELLOW}Maximum total ${coin} the bot can hold (risk limit)${NC}"
    echo -e "${YELLOW}Current value: ${current_max_position}${NC}"
    read -p "Maximum position [${current_max_position}]: " max_position
    max_position=${max_position:-$current_max_position}
    
    echo
    echo -e "${YELLOW}Target balance ratio as decimal (0.5 = 50% ${coin}, 50% ${quote})${NC}"
    echo -e "${YELLOW}Current value: ${current_target_ratio}${NC}"
    read -p "Target inventory ratio (0.01-0.99) [${current_target_ratio}]: " target_ratio
    target_ratio=${target_ratio:-$current_target_ratio}
    
    echo
    echo -e "${YELLOW}Acceptable deviation from target ratio before rebalancing${NC}"
    echo -e "${YELLOW}Current value: ${current_inventory_tolerance}${NC}"
    read -p "Inventory tolerance (0.01-0.5 typical) [${current_inventory_tolerance}]: " inventory_tolerance
    inventory_tolerance=${inventory_tolerance:-$current_inventory_tolerance}
    
    echo
    echo -e "${YELLOW}How often to check market and update orders (in seconds)${NC}"
    echo -e "${YELLOW}Current value: ${current_polling_interval}${NC}"
    read -p "Polling interval (5-60 recommended) [${current_polling_interval}]: " polling_interval
    polling_interval=${polling_interval:-$current_polling_interval}
    
    # Advanced settings
    echo
    echo -e "${BLUE}=== Advanced Settings ===${NC}"
    
    echo
    echo -e "${YELLOW}Filter out orders that deviate too much from market price${NC}"
    echo -e "${YELLOW}Helps protect against extreme outlier orders (0.1 = filter >10% deviation)${NC}"
    echo -e "${YELLOW}Percentage of allowed price deviation from reference (0.1 = 10%)${NC}"
    echo -e "${YELLOW}Set to 0 to disable outlier filtering completely${NC}"
    echo -e "${YELLOW}Current value: ${current_max_deviation}${NC}"
    read -p "Max orderbook deviation (0-1) [${current_max_deviation}]: " max_deviation
    max_deviation=${max_deviation:-$current_max_deviation}
    
    echo
    echo -e "${YELLOW}Reference price source for outlier filtering${NC}"
    echo "  1. vwap (Volume Weighted Average Price - most reliable)"
    echo "  2. nearest_bid (Conservative for selling)"
    echo "  3. nearest_ask (Conservative for buying)"
    echo "  4. ticker_mid (Mid-point between bid/ask)"
    echo "  5. last (Last traded price)"
    
    # Map current value to number
    local ref_num=1
    case $current_filter_ref in
        "vwap") ref_num=1 ;;
        "nearest_bid") ref_num=2 ;;
        "nearest_ask") ref_num=3 ;;
        "ticker_mid") ref_num=4 ;;
        "last") ref_num=5 ;;
    esac
    
    echo -e "${YELLOW}Current: ${current_filter_ref} (option ${ref_num})${NC}"
    read -p "Select reference price (1-5) [${ref_num}]: " ref_choice
    ref_choice=${ref_choice:-$ref_num}
    
    case $ref_choice in
        1) outlier_filter_reference="vwap" ;;
        2) outlier_filter_reference="nearest_bid" ;;
        3) outlier_filter_reference="nearest_ask" ;;
        4) outlier_filter_reference="ticker_mid" ;;
        5) outlier_filter_reference="last" ;;
        *) outlier_filter_reference=$current_filter_ref ;;
    esac
    
    echo
    echo -e "${YELLOW}When ALL orders are filtered out, should the bot use fallback pricing?${NC}"
    echo -e "${YELLOW}If 'n', bot stops placing orders when orderbook is all outliers${NC}"
    read -p "Enable fallback pricing when all orders filtered? (y/n, default y): " enable_fallback
    out_of_range_pricing_fallback=true
    if [[ "$enable_fallback" == "n" ]]; then
        out_of_range_pricing_fallback=false
    fi
    
    echo
    echo "Out-of-range price mode (when all orders filtered):"
    echo "  1. vwap (Volume Weighted Average Price - safest)"
    echo "  2. nearest_bid (Conservative for buying)"
    echo "  3. nearest_ask (Conservative for selling)"
    echo "  4. auto (Adaptive - tries all sources)"
    read -p "Select price mode (1-4, default 1): " price_mode_choice
    price_mode_choice=${price_mode_choice:-1}
    
    case $price_mode_choice in
        1) out_of_range_price_mode="vwap" ;;
        2) out_of_range_price_mode="nearest_bid" ;;
        3) out_of_range_price_mode="nearest_ask" ;;
        4) out_of_range_price_mode="auto" ;;
        *) out_of_range_price_mode="vwap" ;;
    esac
    
    # Create temporary config file with new values
    local temp_config="${config_file}.tmp"
    
    # Get API credentials from existing config (unchanged)
    local api_key=$(grep -A2 "^api:" "$config_file" | grep "key:" | sed 's/.*key:.*"\(.*\)".*/\1/')
    local api_secret=$(grep -A2 "^api:" "$config_file" | grep "secret:" | sed 's/.*secret:.*"\(.*\)".*/\1/')
    
    cat > "$temp_config" << EOF
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
  symbol: "${current_symbol}"
  grid_levels: ${grid_levels}
  grid_spread: ${grid_spread}
  min_order_size: ${min_order_size}
  max_position: ${max_position}
  polling_interval: ${polling_interval}
  target_inventory_ratio: ${target_ratio}
  inventory_tolerance: ${inventory_tolerance}
  max_orderbook_deviation: ${max_deviation}
  outlier_filter_reference: ${outlier_filter_reference}
  out_of_range_pricing_fallback: ${out_of_range_pricing_fallback}
  out_of_range_price_mode: ${out_of_range_price_mode}
EOF
    
    # Replace old config with new
    mv "$temp_config" "$config_file"
    
    echo
    echo -e "${GREEN}Configuration updated successfully!${NC}"
    
    # Check if container is running
    local instance_name="${PREFIX}-$(echo $coin | tr '[:upper:]' '[:lower:]')-${instance_num}"
    local container_status=$(docker inspect -f '{{.State.Status}}' "$instance_name" 2>/dev/null)
    
    if [ "$container_status" = "running" ]; then
        echo
        read -p "Would you like to restart the container to apply changes? (yes/no): " restart_choice
        if [ "$restart_choice" = "yes" ]; then
            echo -e "${YELLOW}Restarting ${instance_name}...${NC}"
            echo -e "${BLUE}The bot will gracefully cancel all open orders before restarting.${NC}"
            docker restart "$instance_name"
            echo -e "${GREEN}Container restarted with new configuration!${NC}"
            sleep 2
        else
            echo -e "${YELLOW}Note: Changes will take effect on next container restart.${NC}"
            sleep 2
        fi
    else
        echo -e "${YELLOW}Note: Container is not running. Start it to use the new configuration.${NC}"
        sleep 2
    fi
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
    echo -e "${BLUE}Key Features:${NC}"
    echo "• Grid-based order placement above and below market price"
    echo "• Automatic inventory rebalancing to maintain target ratios"
    echo "• Smart filtering to ignore extreme outlier orders"
    echo "• Graceful shutdown with automatic order cancellation"
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
    echo "Make sure to enable trading permissions for your API Public Key."
    
    # Check if there are existing API keys to import
    local existing_keys=($(get_existing_api_keys))
    local api_key=""
    local api_secret=""
    
    if [ ${#existing_keys[@]} -gt 0 ]; then
        echo
        echo "You have existing API credentials from other configurations."
        echo "Would you like to:"
        echo "1) Import existing API credentials"
        echo "2) Enter new API credentials manually"
        echo
        
        while true; do
            read -p "Choose option (1-2): " cred_choice
            case $cred_choice in
                1)
                    local import_result=$(import_api_credentials "$coin")
                    if [ $? -eq 0 ] && [ -n "$import_result" ]; then
                        # API credentials were successfully imported
                        IFS='|' read -r api_key api_secret <<< "$import_result"
                        echo -e "${GREEN}API credentials imported successfully!${NC}"
                        echo
                        break
                    else
                        # User chose to enter manually or import failed
                        echo -e "${YELLOW}Proceeding with manual entry...${NC}"
                        echo
                        read -p "Enter your API Public Key: " api_key
                        read -s -p "Enter your API Private Key: " api_secret
                        echo
                        break
                    fi
                    ;;
                2)
                    echo
                    read -p "Enter your API Public Key: " api_key
                    read -s -p "Enter your API Private Key: " api_secret
                    echo
                    break
                    ;;
                *)
                    echo -e "${RED}Invalid choice! Please select 1 or 2.${NC}"
                    ;;
            esac
        done
    else
        # No existing API keys, proceed with manual entry
        echo
        read -p "Enter your API Public Key: " api_key
        read -s -p "Enter your API Private Key: " api_secret
        echo
    fi
    
    # Check if API key is already used for this specific coin (only if we have values)
    if [ -n "$api_key" ] && check_api_key_usage "$api_key" "$coin"; then
        echo -e "\n${RED}Error: This API Public Key is already being used for another ${coin} instance!${NC}"
        echo "You cannot use the same API key for multiple instances of the same coin."
        echo "Either use a different API key or manage the existing ${coin} instance instead."
        echo -e "${YELLOW}Press Enter to return to main menu...${NC}"
        read
        return
    fi
    
    # Get trading parameters
    echo
    echo -e "${BLUE}=== Trading Parameters ===${NC}"
    echo
    
    echo -e "${YELLOW}Number of buy/sell orders to place on each side of the market${NC}"
    read -p "Grid levels (1-20 recommended, default: 3): " grid_levels
    grid_levels=${grid_levels:-3}
    
    echo
    echo -e "${YELLOW}Distance between each order level as a decimal (0.001 = 0.1%)${NC}"
    read -p "Grid spread (0.0005-0.01 typical, default: 0.0005): " grid_spread
    grid_spread=${grid_spread:-0.0005}
    
    echo
    echo -e "${YELLOW}Smallest order size the exchange allows for ${coin}${NC}"
    read -p "Minimum order size (default: 0.1): " min_order_size
    min_order_size=${min_order_size:-0.1}
    
    echo
    echo -e "${YELLOW}Maximum total ${coin} the bot can hold (risk limit)${NC}"
    read -p "Maximum position (default: 0.5): " max_position
    max_position=${max_position:-0.5}
    
    echo
    echo -e "${YELLOW}Target balance ratio as decimal (0.5 = 50% ${coin}, 50% ${quote})${NC}"
    read -p "Target inventory ratio (0.01-0.99, default: 0.5): " target_ratio
    target_ratio=${target_ratio:-0.5}
    
    echo
    echo -e "${YELLOW}Acceptable deviation from target ratio before rebalancing${NC}"
    read -p "Inventory tolerance (0.01-0.5 typical, default: 0.1): " inventory_tolerance
    inventory_tolerance=${inventory_tolerance:-0.1}
    
    # Add outlier filtering parameter
    echo
    echo -e "${YELLOW}=== Advanced Settings ===${NC}"
    echo
    echo -e "${YELLOW}Percentage of allowed price deviation from reference (0.1 = 10%)${NC}"
    echo -e "${YELLOW}Set to 0 to disable outlier filtering completely${NC}"
    echo -e "${YELLOW}Current value: ${current_max_deviation}${NC}"
    read -p "Max orderbook deviation (0-1) [${current_max_deviation}]: " max_deviation
    max_deviation=${max_deviation:-$current_max_deviation}
    
    echo
    echo -e "${YELLOW}Reference price source for outlier filtering${NC}"
    echo "  1. vwap (Volume Weighted Average Price - most reliable)"
    echo "  2. nearest_bid (Conservative for selling)"
    echo "  3. nearest_ask (Conservative for buying)"
    echo "  4. ticker_mid (Mid-point between bid/ask)"
    echo "  5. last (Last traded price)"
    
    # Map current value to number
    local ref_num=1
    case $current_filter_ref in
        "vwap") ref_num=1 ;;
        "nearest_bid") ref_num=2 ;;
        "nearest_ask") ref_num=3 ;;
        "ticker_mid") ref_num=4 ;;
        "last") ref_num=5 ;;
    esac
    
    echo -e "${YELLOW}Current: ${current_filter_ref} (option ${ref_num})${NC}"
    read -p "Select reference price (1-5) [${ref_num}]: " ref_choice
    ref_choice=${ref_choice:-$ref_num}
    
    case $ref_choice in
        1) outlier_filter_reference="vwap" ;;
        2) outlier_filter_reference="nearest_bid" ;;
        3) outlier_filter_reference="nearest_ask" ;;
        4) outlier_filter_reference="ticker_mid" ;;
        5) outlier_filter_reference="last" ;;
        *) outlier_filter_reference=$current_filter_ref ;;
    esac
    
    echo
    echo -e "${YELLOW}When ALL orders are filtered out, should the bot use fallback pricing?${NC}"
    echo -e "${YELLOW}If 'n', bot stops placing orders when orderbook is all outliers${NC}"
    read -p "Enable fallback pricing when all orders filtered? (y/n, default y): " enable_fallback
    out_of_range_pricing_fallback=true
    if [[ "$enable_fallback" == "n" ]]; then
        out_of_range_pricing_fallback=false
    fi
    
    echo
    echo "Out-of-range price mode (when all orders filtered):"
    echo "  1. vwap (Volume Weighted Average Price - safest)"
    echo "  2. nearest_bid (Conservative for buying)"
    echo "  3. nearest_ask (Conservative for selling)"
    echo "  4. auto (Adaptive - tries all sources)"
    read -p "Select price mode (1-4, default 1): " price_mode_choice
    price_mode_choice=${price_mode_choice:-1}
    
    case $price_mode_choice in
        1) out_of_range_price_mode="vwap" ;;
        2) out_of_range_price_mode="nearest_bid" ;;
        3) out_of_range_price_mode="nearest_ask" ;;
        4) out_of_range_price_mode="auto" ;;
        *) out_of_range_price_mode="vwap" ;;
    esac
    
    # Validate max_deviation
    if [[ "$max_deviation" != "0" ]] && (( $(echo "$max_deviation < 0" | bc -l) )); then
        echo -e "${YELLOW}Invalid deviation, using default 0.1 (10%)${NC}"
        max_deviation=0.1
    fi
    
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
    
    # Find next instance number (check lowercase version for Docker)
    instance_num=1
    coin_lower=$(echo "$coin" | tr '[:upper:]' '[:lower:]')
    while docker ps -a --format "{{.Names}}" | grep -q "^${PREFIX}-${coin_lower}-${instance_num}$"; do
        ((instance_num++))
    done
    
    instance_name="${PREFIX}-${coin}-${instance_num}"
    # Docker requires lowercase names
    instance_name_lower="${PREFIX}-$(echo $coin | tr '[:upper:]' '[:lower:]')-${instance_num}"
    
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
  inventory_tolerance: ${inventory_tolerance}
  max_orderbook_deviation: ${max_deviation}
  outlier_filter_reference: ${outlier_filter_reference}
  out_of_range_pricing_fallback: ${out_of_range_pricing_fallback}
  out_of_range_price_mode: ${out_of_range_price_mode}
EOF
    
    # Create docker-compose file for this instance
    compose_file="${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
    
    # Get absolute paths for volumes
    config_file_abs=$(pwd)/${config_file}
    data_dir_abs=$(pwd)/${DATA_DIR}/${coin}-${instance_num}
    
    cat > "$compose_file" << EOF
services:
  ${instance_name_lower}:
    build: 
      context: $(pwd)
      network: host
    container_name: ${instance_name_lower}
    network_mode: host
    volumes:
      - ${config_file_abs}:/app/config.yaml:ro
      - ${data_dir_abs}:/app/data
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
    echo -e "${BLUE}Instance name:${NC} ${instance_name_lower}"
    echo -e "${BLUE}Trading pair:${NC} ${symbol}"
    echo -e "${BLUE}Config file:${NC} ${config_file}"
    echo -e "${BLUE}Logs:${NC} ${DATA_DIR}/${coin}-${instance_num}/market_maker.log"
    echo
    echo "The bot will:"
    echo "• Place buy orders below the current market price"
    echo "• Place sell orders above the current market price"
    echo "• Automatically adjust orders as the market moves"
    echo "• Maintain your target inventory ratio"
    
    # Show outlier filtering status
    if [ "$max_deviation" != "0" ]; then
        local deviation_pct=$(echo "scale=1; $max_deviation * 100" | bc)
        echo "• Filter out orders more than ${deviation_pct}% from market price"
    fi
    
    echo
    echo "You can check the logs with:"
    echo -e "${YELLOW}docker logs ${instance_name_lower}${NC}"
    echo
    echo -e "${YELLOW}Press Enter to return to main menu...${NC}"
    read
}

# Function to manage orphaned configurations
manage_orphaned_config() {
    local orphan_id=$1
    local coin=$(echo "$orphan_id" | cut -d'-' -f1)
    local instance_num=$(echo "$orphan_id" | cut -d'-' -f2)
    
    echo
    echo -e "${YELLOW}Orphaned Configuration: ${coin}-${instance_num}${NC}"
    echo -e "${BLUE}Config file:${NC} ${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
    echo -e "${BLUE}Data directory:${NC} ${DATA_DIR}/${coin}-${instance_num}"
    
    # Check if data directory exists
    if [ -d "${DATA_DIR}/${coin}-${instance_num}" ]; then
        local data_size=$(du -sh "${DATA_DIR}/${coin}-${instance_num}" 2>/dev/null | cut -f1)
        echo -e "${BLUE}Data size:${NC} ${data_size:-unknown}"
    fi
    
    # Show trading pair from config
    if [ -f "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml" ]; then
        local symbol=$(grep "symbol:" "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml" | awk '{print $2}' | tr -d '"')
        echo -e "${BLUE}Trading Pair:${NC} ${symbol}"
    fi
    
    echo
    echo "What would you like to do?"
    echo "1) Recreate container with existing data"
    echo "2) Recreate container with fresh data"
    echo "3) Delete configuration only"
    echo "4) Delete everything (config and data)"
    echo "5) View configuration"
    echo "6) Run simulation (dry run)"
    echo "7) Back to main menu"
    
    read -p "Enter your choice (1-7): " choice
    
    case $choice in
        1)
            echo -e "${YELLOW}Recreating container with existing data...${NC}"
            
            # Check if compose file exists, create if needed
            local compose_file="${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
            if [ ! -f "$compose_file" ]; then
                echo -e "${YELLOW}Recreating docker-compose file...${NC}"
                
                # Get absolute paths
                local config_file_abs=$(pwd)/${CONFIG_DIR}/${coin}-${instance_num}-config.yaml
                local data_dir_abs=$(pwd)/${DATA_DIR}/${coin}-${instance_num}
                local instance_name_lower="${PREFIX}-$(echo $coin | tr '[:upper:]' '[:lower:]')-${instance_num}"
                
                cat > "$compose_file" << EOF
services:
  ${instance_name_lower}:
    build: 
      context: $(pwd)
      network: host
    container_name: ${instance_name_lower}
    network_mode: host
    volumes:
      - ${config_file_abs}:/app/config.yaml:ro
      - ${data_dir_abs}:/app/data
    restart: on-failure:3
    tty: true
    stdin_open: true
    stop_grace_period: 60s
EOF
            fi
            
            docker compose -f "$compose_file" up -d --build
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}Container recreated successfully!${NC}"
            else
                echo -e "${RED}Failed to recreate container.${NC}"
            fi
            sleep 3
            ;;
        2)
            echo -e "${YELLOW}Recreating container with fresh data...${NC}"
            
            # Remove old data
            rm -rf "${DATA_DIR}/${coin}-${instance_num}"
            mkdir -p "${DATA_DIR}/${coin}-${instance_num}"
            
            # Recreate compose file if needed
            local compose_file="${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
            if [ ! -f "$compose_file" ]; then
                # Same as option 1, create compose file
                local config_file_abs=$(pwd)/${CONFIG_DIR}/${coin}-${instance_num}-config.yaml
                local data_dir_abs=$(pwd)/${DATA_DIR}/${coin}-${instance_num}
                local instance_name_lower="${PREFIX}-$(echo $coin | tr '[:upper:]' '[:lower:]')-${instance_num}"
                
                cat > "$compose_file" << EOF
services:
  ${instance_name_lower}:
    build: 
      context: $(pwd)
      network: host
    container_name: ${instance_name_lower}
    network_mode: host
    volumes:
      - ${config_file_abs}:/app/config.yaml:ro
      - ${data_dir_abs}:/app/data
    restart: on-failure:3
    tty: true
    stdin_open: true
    stop_grace_period: 60s
EOF
            fi
            
            docker compose -f "$compose_file" up -d --build
            if [ $? -eq 0 ]; then
                echo -e "${GREEN}Container recreated with fresh data!${NC}"
            else
                echo -e "${RED}Failed to recreate container.${NC}"
            fi
            sleep 3
            ;;
        3)
            read -p "Delete configuration files only? (yes/no): " confirm
            if [ "$confirm" = "yes" ]; then
                rm -f "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                rm -f "${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
                echo -e "${GREEN}Configuration files deleted. Data preserved in ${DATA_DIR}/${coin}-${instance_num}${NC}"
                sleep 2
            fi
            ;;
        4)
            read -p "Delete everything (config and data)? (yes/no): " confirm
            if [ "$confirm" = "yes" ]; then
                rm -f "${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
                rm -f "${CONFIG_DIR}/${coin}-${instance_num}-docker-compose.yml"
                rm -rf "${DATA_DIR}/${coin}-${instance_num}"
                echo -e "${GREEN}Everything deleted!${NC}"
                sleep 2
            fi
            ;;
        5)
            local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
            if [ -f "$config_file" ]; then
                echo -e "\n${BLUE}Configuration:${NC}"
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
            local config_file="${CONFIG_DIR}/${coin}-${instance_num}-config.yaml"
            if [ -f "$config_file" ]; then
                echo -e "\n${BLUE}Running market simulation for ${coin}-${instance_num}...${NC}"
                echo -e "${YELLOW}This is a dry run - no actual orders will be placed.${NC}"
                echo -e "${YELLOW}The simulation shows what the bot would do in one market cycle.${NC}\n"
                
                # Check if scripts/simulate_bot_cycle.py exists
                if [ -f "scripts/simulate_bot_cycle.py" ]; then
                    # Run the simulation
                    python3 scripts/simulate_bot_cycle.py "$config_file"
                    
                    echo -e "\n${GREEN}Simulation complete!${NC}"
                    echo -e "${BLUE}This shows what would happen if you recreate this bot.${NC}"
                    echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                    read
                else
                    echo -e "${RED}Error: scripts/simulate_bot_cycle.py not found!${NC}"
                    echo -e "${YELLOW}Make sure you're running this from the project directory.${NC}"
                    echo -e "\n${YELLOW}Press Enter to continue...${NC}"
                    read
                fi
            else
                echo -e "${RED}Configuration file not found!${NC}"
                sleep 2
            fi
            ;;
        7)
            return
            ;;
        *)
            echo -e "${RED}Invalid choice!${NC}"
            sleep 2
            ;;
    esac
}

# Main menu
main_menu() {
    while true; do
        clear
        echo -e "${GREEN}=== Market Maker Manager ===${NC}"
        echo
        
        # Get existing instances
        instances=($(get_existing_instances))
        
        # Check for orphaned data
        orphaned=($(detect_orphaned_data))
        
        if [ ${#instances[@]} -gt 0 ] || [ ${#orphaned[@]} -gt 0 ]; then
            echo "Existing instances:"
            echo
            for i in "${!instances[@]}"; do
                local status=$(docker ps -a --filter "name=^${instances[$i]}$" --format "{{.Status}}")
                local state=$(docker inspect -f '{{.State.Status}}' "${instances[$i]}" 2>/dev/null)
                local exit_code=$(docker inspect -f '{{.State.ExitCode}}' "${instances[$i]}" 2>/dev/null)
                
                # Color code based on status
                if [ "$state" = "running" ]; then
                    echo -e "$((i+1))) ${GREEN}${instances[$i]}${NC} - $status"
                elif [ "$state" = "exited" ] && [ "$exit_code" != "0" ]; then
                    echo -e "$((i+1))) ${RED}${instances[$i]}${NC} - $status ${RED}(needs attention)${NC}"
                else
                    echo -e "$((i+1))) ${YELLOW}${instances[$i]}${NC} - $status"
                fi
            done
            
            # Show orphaned data if any
            if [ ${#orphaned[@]} -gt 0 ]; then
                echo
                echo -e "${YELLOW}Orphaned configurations (no container):${NC}"
                local orphan_start=$((${#instances[@]}+1))
                for i in "${!orphaned[@]}"; do
                    echo -e "$((orphan_start+i))) ${YELLOW}${orphaned[$i]}${NC} - ${BLUE}[Config & Data Only]${NC}"
                done
                
                echo
                echo "$((orphan_start+${#orphaned[@]}))) Create new instance"
                echo "$((orphan_start+${#orphaned[@]}+1))) Exit"
            else
                echo
                echo "$((${#instances[@]}+1))) Create new instance"
                echo "$((${#instances[@]}+2))) Exit"
            fi
            
            read -p "Enter your choice: " choice
            
            if [ "$choice" -ge 1 ] && [ "$choice" -le ${#instances[@]} ]; then
                manage_instance "${instances[$((choice-1))]}"
            elif [ ${#orphaned[@]} -gt 0 ]; then
                # Handle orphaned data choices
                local orphan_start=$((${#instances[@]}+1))
                local orphan_end=$((orphan_start+${#orphaned[@]}-1))
                
                if [ "$choice" -ge "$orphan_start" ] && [ "$choice" -le "$orphan_end" ]; then
                    # Manage orphaned config
                    local orphan_index=$((choice-orphan_start))
                    manage_orphaned_config "${orphaned[$orphan_index]}"
                elif [ "$choice" -eq $((orphan_start+${#orphaned[@]})) ]; then
                    create_new_instance
                elif [ "$choice" -eq $((orphan_start+${#orphaned[@]}+1)) ]; then
                    echo -e "${GREEN}Goodbye!${NC}"
                    exit 0
                else
                    echo -e "${RED}Invalid choice!${NC}"
                    sleep 2
                fi
            else
                # No orphaned data
                if [ "$choice" -eq $((${#instances[@]}+1)) ]; then
                    create_new_instance
                elif [ "$choice" -eq $((${#instances[@]}+2)) ]; then
                    echo -e "${GREEN}Goodbye!${NC}"
                    exit 0
                else
                    echo -e "${RED}Invalid choice!${NC}"
                    sleep 2
                fi
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