# ADAMS MCP Server Dockerfile
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY adams_mcp.py .
COPY adams_client_v3.py .
COPY server.json .

# Create directories for logs and downloads
RUN mkdir -p /app/logs /app/downloads

# Set environment variables (can be overridden at runtime)
ENV PYTHONUNBUFFERED=1 \
    LOG_FILE=/app/logs/mcp_server.log

# Expose MCP server port if needed (MCP typically uses stdio, but leaving for HTTP mode)
# EXPOSE 8080

# Health check (optional - for when MCP supports HTTP health endpoints)
# HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
#   CMD curl -f http://localhost:8080/health || exit 1

# Run the MCP server
CMD ["python", "-u", "adams_mcp.py"]
