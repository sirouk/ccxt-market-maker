#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Docker Network Troubleshooting ===${NC}"
echo

# Function to test DNS resolution
test_dns() {
    echo -e "${YELLOW}Testing DNS resolution...${NC}"
    
    # Test system DNS
    if nslookup google.com >/dev/null 2>&1; then
        echo -e "${GREEN}✓ System DNS is working${NC}"
    else
        echo -e "${RED}✗ System DNS is not working${NC}"
        echo "Please check your network connection"
        exit 1
    fi
    
    # Test Docker DNS
    if docker run --rm alpine nslookup google.com >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Docker DNS is working${NC}"
    else
        echo -e "${RED}✗ Docker DNS is not working${NC}"
        echo -e "${YELLOW}Attempting to fix...${NC}"
        return 1
    fi
    
    return 0
}

# Function to fix Docker daemon DNS
fix_docker_daemon() {
    echo -e "${YELLOW}Configuring Docker daemon DNS...${NC}"
    
    # Create daemon.json if it doesn't exist
    if [ ! -f /etc/docker/daemon.json ]; then
        echo -e "${YELLOW}Creating Docker daemon configuration...${NC}"
        sudo tee /etc/docker/daemon.json > /dev/null <<EOF
{
    "dns": ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
}
EOF
    else
        echo -e "${YELLOW}Docker daemon configuration already exists${NC}"
        echo "Current configuration:"
        cat /etc/docker/daemon.json
        echo
        read -p "Do you want to update it with DNS settings? (y/n): " update
        if [ "$update" = "y" ]; then
            # Backup existing config
            sudo cp /etc/docker/daemon.json /etc/docker/daemon.json.backup
            
            # Update with DNS settings (this is simplified, in production you'd merge JSON properly)
            echo -e "${YELLOW}Please manually add the following DNS settings to /etc/docker/daemon.json:${NC}"
            echo '"dns": ["8.8.8.8", "8.8.4.4", "1.1.1.1"]'
        fi
    fi
    
    # Restart Docker
    echo -e "${YELLOW}Restarting Docker service...${NC}"
    if command -v systemctl &> /dev/null; then
        sudo systemctl restart docker
    elif command -v service &> /dev/null; then
        sudo service docker restart
    else
        echo -e "${RED}Could not restart Docker automatically. Please restart Docker manually.${NC}"
    fi
    
    sleep 5
}

# Main execution
if ! test_dns; then
    fix_docker_daemon
    
    # Test again
    if test_dns; then
        echo -e "${GREEN}✓ Docker DNS fixed successfully!${NC}"
    else
        echo -e "${RED}Docker DNS still not working. Additional troubleshooting needed.${NC}"
        echo
        echo "Try these manual fixes:"
        echo "1. Check if you're behind a corporate firewall/proxy"
        echo "2. Restart your machine"
        echo "3. Reset Docker to factory defaults"
        echo "4. Check your network adapter settings"
    fi
fi

echo
echo -e "${BLUE}Alternative Solutions:${NC}"
echo "1. Build with host network mode:"
echo -e "   ${YELLOW}docker build --network=host -t market-maker .${NC}"
echo
echo "2. Use a different DNS in your Dockerfile:"
echo -e "   ${YELLOW}RUN echo 'nameserver 1.1.1.1' > /etc/resolv.conf${NC}"
echo
echo "3. If behind a proxy, set Docker proxy:"
echo -e "   ${YELLOW}https://docs.docker.com/network/proxy/${NC}" 