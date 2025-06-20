FROM python:3.12-slim

WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Set Python path to include src directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Run via entrypoint
ENTRYPOINT ["./entrypoint.sh"]
