# Use Python 3.12 slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy requirements first for better caching
COPY requirements.txt .

# Create virtual environment with Python 3.12
RUN uv venv --python 3.12 --seed

# Install Python dependencies in the virtual environment
RUN . .venv/bin/activate && \
    uv pip install -r requirements.txt

# Copy application code
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Set Python path to include src directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Run the entrypoint script
CMD ["./entrypoint.sh"]
