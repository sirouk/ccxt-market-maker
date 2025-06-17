#!/bin/bash

# Ensure data directory exists
mkdir -p /app/data

echo "Starting market maker bot..."

# Handle shutdown signals properly
cleanup() {
    echo "Received shutdown signal, sending SIGTERM to Python process..."
    if [ ! -z "$PYTHON_PID" ]; then
        kill -TERM "$PYTHON_PID"
        echo "Waiting for graceful shutdown..."
        wait "$PYTHON_PID"
        echo "Python process has terminated"
    fi
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Run Python in background so we can handle signals
python main.py &
PYTHON_PID=$!

echo "Market maker bot started with PID: $PYTHON_PID"

# Wait for the Python process to finish
wait "$PYTHON_PID"
EXIT_CODE=$?

echo "Market maker bot finished with exit code: $EXIT_CODE"
exit $EXIT_CODE
