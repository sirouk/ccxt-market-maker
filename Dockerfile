FROM python:3.12-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies with retry logic and timeout settings
RUN pip install --no-cache-dir --timeout 120 --retries 5 -r requirements.txt || \
    (echo "Retrying with different index..." && \
     pip install --no-cache-dir --timeout 120 --retries 5 --index-url https://pypi.python.org/simple/ -r requirements.txt)

# Copy the rest of the application
COPY . .

# Create data directory
RUN mkdir -p /app/data

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Run the entrypoint script
ENTRYPOINT ["./entrypoint.sh"]
