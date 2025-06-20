#!/bin/bash
set -e

# Ensure data directory exists
mkdir -p /app/data

# Function to handle shutdown
shutdown() {
    echo "Received shutdown signal, stopping market maker..."
    if [ ! -z "$BOT_PID" ]; then
        # Send SIGTERM to the Python process
        kill -TERM $BOT_PID 2>/dev/null || true
        
        # Wait for graceful shutdown (up to 60 seconds)
        timeout=60
        while [ $timeout -gt 0 ] && kill -0 $BOT_PID 2>/dev/null; do
            sleep 1
            ((timeout--))
        done
        
        # If still running, force kill
        if kill -0 $BOT_PID 2>/dev/null; then
            echo "Forcing shutdown..."
            kill -KILL $BOT_PID 2>/dev/null || true
        fi
    fi
    exit 0
}

# Register the shutdown function to handle signals
trap shutdown SIGTERM SIGINT

# Start the market maker bot
echo "Starting market maker bot..."
python3 -m src.bot.main &
BOT_PID=$!
echo "Market maker bot started with PID: $BOT_PID"

# Wait for the bot process
wait $BOT_PID
