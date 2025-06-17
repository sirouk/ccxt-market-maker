# This file is deprecated - configuration loading is now handled in config.py
# Remove this file as it's no longer used in the refactored architecture
import os
import sys

import yaml

def load_config():
    """Load configuration from YAML file and set environment variables."""
    try:
        # Check if config file exists
        if not os.path.exists('config.yaml'):
            print("Error: config.yaml file not found")
            return False

        with open('config.yaml', 'r') as file:
            config = yaml.safe_load(file)

        if not config:
            print("Error: config.yaml is empty or not valid YAML")
            return False

        # Set API credentials as environment variables
        api_config = config.get('api', {})
        api_key = api_config.get('key', '')
        api_secret = api_config.get('secret', '')

        if not api_key or not api_secret:
            print("Warning: API key or secret missing in config.yaml")

        # Export to environment variables
        os.environ["API_KEY"] = api_key
        os.environ["API_SECRET"] = api_secret

        # Set storage config with default paths if not specified
        storage_config = config.get('storage', {})
        db_path = storage_config.get('db_path', 'data/market_maker.db')
        log_file = storage_config.get('log_file', 'data/market_maker.log')

        os.environ["DB_PATH"] = db_path
        os.environ["LOG_FILE"] = log_file

        # Load bot configuration
        bot_config = config.get('bot_config', {})
        for key, value in bot_config.items():
            os.environ[key.upper()] = str(value)

        print(f"Config loaded successfully. Running bot for {bot_config.get('symbol', 'Unknown')}.")

        # Debug output to verify environment
        print(f"Using storage: DB={os.environ.get('DB_PATH')}, Log={os.environ.get('LOG_FILE')}")
        print(f"API credentials: {'Set' if api_key and api_secret else 'Missing'}")

        return bool(api_key and api_secret)

    except Exception as e:
        print(f"Error loading config: {e}")
        return False

if __name__ == "__main__":
    success = load_config()
    sys.exit(0 if success else 1)
