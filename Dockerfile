FROM python:3.12-slim

# Set DNS servers explicitly for build time
RUN echo "nameserver 8.8.8.8" > /etc/resolv.conf && \
    echo "nameserver 8.8.4.4" >> /etc/resolv.conf

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies with retry logic and timeout settings
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt

# Copy the rest of the application
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Run the entrypoint script
ENTRYPOINT ["./entrypoint.sh"]
