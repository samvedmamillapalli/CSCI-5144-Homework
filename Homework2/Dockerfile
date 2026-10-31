
# Use lightweight Python Alpine image
FROM python:3.12-alpine

# Set working directory inside container
WORKDIR /app

# Copy dependency file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY Server.py .
COPY kv_store.json .

# Expose application port
EXPOSE 8080

# Start the key-value store server
CMD ["python", "Server.py"]