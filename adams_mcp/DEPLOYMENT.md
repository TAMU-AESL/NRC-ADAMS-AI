# ADAMS MCP Server - Deployment Guide

This guide explains how to deploy and run the ADAMS MCP server in any environment.

## Understanding MCP Server Architecture

### How MCP Servers Work

MCP (Model Context Protocol) servers are **tools that AI assistants can use**. There are two main communication modes:

1. **stdio (Standard Input/Output)** - Most common
   - The MCP server communicates via stdin/stdout
   - The AI assistant (like Claude Desktop) starts the server as a subprocess
   - No network ports needed
   - **This is what ADAMS MCP uses**

2. **HTTP/SSE** - Alternative
   - Server runs on a network port
   - AI assistant connects via HTTP
   - Better for remote servers

### Where This Server Runs

The ADAMS MCP server **runs alongside your AI assistant** (not as a standalone web service). When you configure it in Claude Desktop or another MCP client, the client will:

1. Start the Python process
2. Communicate via stdio
3. Call the tools when needed
4. Shut down when done

---

## Deployment Options

You have **4 ways** to run this MCP server:

### Option 1: Direct Python Execution (Simplest)
### Option 2: Using the Startup Script (Recommended for Development)
### Option 3: Docker Container (Portable)
### Option 4: Docker Compose (Production-Ready)

---

## Option 1: Direct Python Execution

**Best for:** Testing, development, single-machine use

### Steps:

```bash
# 1. Navigate to project directory
cd /workspace/projects/adams_mcp

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) Configure environment
cp .env.example .env
nano .env  # Add your Google API credentials

# 4. Run the server
python adams_mcp.py
```

### Pros:
- Simplest setup
- No containerization overhead
- Easy to debug

### Cons:
- Requires Python installed on host
- Dependency conflicts possible
- Not isolated from host system

---

## Option 2: Using the Startup Script

**Best for:** Local development with virtual environment isolation

### Steps:

```bash
# 1. Navigate to project directory
cd /workspace/projects/adams_mcp

# 2. (Optional) Configure environment
cp .env.example .env
nano .env  # Add your Google API credentials

# 3. Run the startup script
./start_server.sh
```

The script automatically:
- Creates a virtual environment (venv)
- Installs dependencies
- Creates necessary directories
- Starts the server

### Pros:
- Isolated Python environment
- Automatic dependency management
- Clean and repeatable
- No Docker required

### Cons:
- Still requires Python on host
- Not fully containerized

---

## Option 3: Docker Container

**Best for:** Portable deployment, consistent environments

### Steps:

```bash
# 1. Navigate to project directory
cd /workspace/projects/adams_mcp

# 2. Build the Docker image
docker build -t adams-mcp:latest .

# 3. Run the container
docker run -d \
  --name adams-mcp \
  -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  -e GOOGLE_CX="${GOOGLE_CX}" \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/logs:/app/logs \
  adams-mcp:latest
```

### With .env file:

```bash
docker run -d \
  --name adams-mcp \
  --env-file .env \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/logs:/app/logs \
  adams-mcp:latest
```

### View logs:
```bash
docker logs -f adams-mcp
```

### Stop the container:
```bash
docker stop adams-mcp
docker rm adams-mcp
```

### Pros:
- Complete isolation
- Consistent across environments
- No host Python dependencies
- Easy to share and deploy

### Cons:
- Requires Docker installed
- Slightly more complex

---

## Option 4: Docker Compose (Recommended for Production)

**Best for:** Production deployments, easy management, persistence

### Steps:

```bash
# 1. Navigate to project directory
cd /workspace/projects/adams_mcp

# 2. Configure environment
cp .env.example .env
nano .env  # Add your Google API credentials

# 3. Start the service
docker-compose up -d

# 4. View logs
docker-compose logs -f

# 5. Stop the service
docker-compose down
```

### Additional Commands:

```bash
# Rebuild after code changes
docker-compose up -d --build

# Restart service
docker-compose restart

# View service status
docker-compose ps

# Execute commands in container
docker-compose exec adams-mcp python -c "from adams_client_v3 import AdamsClient; print('OK')"
```

### Pros:
- Easy to start/stop/restart
- Automatic restart on failure
- Volume management for persistence
- Resource limits configured
- Production-ready logging

### Cons:
- Requires Docker and Docker Compose
- Slightly more configuration

---

## Configuring with AI Assistants

### Claude Desktop Configuration

**Important**: MCP servers communicate via stdio, so you **configure them in Claude Desktop's settings**, not as a running server.

Add to your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

#### Option A: Direct Python (if using virtual environment)

```json
{
  "mcpServers": {
    "adams": {
      "command": "/workspace/projects/adams_mcp/venv/bin/python",
      "args": ["/workspace/projects/adams_mcp/adams_mcp.py"],
      "env": {
        "GOOGLE_API_KEY": "your_api_key_here",
        "GOOGLE_CX": "your_cx_here"
      }
    }
  }
}
```

#### Option B: System Python

```json
{
  "mcpServers": {
    "adams": {
      "command": "python3",
      "args": ["/workspace/projects/adams_mcp/adams_mcp.py"],
      "env": {
        "GOOGLE_API_KEY": "your_api_key_here",
        "GOOGLE_CX": "your_cx_here"
      }
    }
  }
}
```

#### Option C: Using Docker

```json
{
  "mcpServers": {
    "adams": {
      "command": "docker",
      "args": [
        "run",
        "--rm",
        "-i",
        "--env-file",
        "/workspace/projects/adams_mcp/.env",
        "adams-mcp:latest"
      ]
    }
  }
}
```

**Note**: Replace `/workspace/projects/adams_mcp` with your actual path.

---

## Environment Variables

All deployment methods support these environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | No | Google Custom Search API key (for hybrid search) |
| `GOOGLE_CX` | No | Google Custom Search Engine ID |
| `PYTHONUNBUFFERED` | No | Set to `1` for real-time log output (Docker default) |

---

## Verifying Your Deployment

### Test 1: Check logs

```bash
# Direct Python
cat logs/mcp_server.log

# Docker
docker logs adams-mcp

# Docker Compose
docker-compose logs
```

You should see:
```
ADAMS MCP server initializing...
✅ Starting ADAMS MCP server with Google hybrid search...
```

### Test 2: Test imports (if using Docker)

```bash
# Docker
docker exec adams-mcp python -c "from adams_client_v3 import AdamsClient; print('✓ Imports OK')"

# Docker Compose
docker-compose exec adams-mcp python -c "from adams_client_v3 import AdamsClient; print('✓ Imports OK')"
```

### Test 3: Run test suite

```bash
# Direct Python
python test_adams.py

# Docker
docker run --rm adams-mcp:latest python test_adams.py

# Docker Compose
docker-compose run --rm adams-mcp python test_adams.py
```

---

## Troubleshooting

### Issue: Dependencies not found

**Solution**: Reinstall dependencies
```bash
pip install -r requirements.txt  # Direct
docker-compose up --build  # Docker Compose
```

### Issue: Google Search not working

**Solution**: Check environment variables
```bash
# Direct Python
cat .env

# Docker
docker exec adams-mcp env | grep GOOGLE

# Docker Compose
docker-compose exec adams-mcp env | grep GOOGLE
```

### Issue: Cannot connect to ADAMS API

**Solution**: Check network connectivity
```bash
curl -I https://adams.nrc.gov/wba/
```

### Issue: Permission denied errors

**Solution**: Fix file permissions
```bash
chmod +x start_server.sh
chmod -R 755 downloads logs
```

### Issue: Port already in use (if using HTTP mode)

**Solution**: Stop conflicting service or change port
```bash
# Find what's using the port
lsof -i :8080

# Change port in docker-compose.yml
```

---

## Production Deployment Checklist

- [ ] Dependencies installed (`requirements.txt`)
- [ ] Environment variables configured (`.env` or system)
- [ ] Directories created (`logs/`, `downloads/`)
- [ ] File permissions set correctly
- [ ] Test suite passes (`python test_adams.py`)
- [ ] Logs are readable and error-free
- [ ] Claude Desktop config updated with correct paths
- [ ] Docker image built (if using Docker)
- [ ] Restart policy configured (if using Docker Compose)
- [ ] Resource limits set (if using Docker Compose)

---

## Updating the Server

### Direct Python:
```bash
git pull  # If using git
pip install -r requirements.txt --upgrade
```

### Docker:
```bash
docker-compose down
docker-compose up -d --build
```

---

## Monitoring and Logs

### Log Locations:

| Method | Log Location |
|--------|-------------|
| Direct Python | `./logs/mcp_server.log` |
| Docker | `docker logs adams-mcp` |
| Docker Compose | `docker-compose logs` or `./logs/mcp_server.log` |

### Monitoring Commands:

```bash
# Tail logs in real-time (Direct Python)
tail -f logs/mcp_server.log

# Docker
docker logs -f adams-mcp

# Docker Compose
docker-compose logs -f
```

---

## Security Considerations

1. **Never commit `.env` files** - Use `.env.example` as template
2. **Use environment variables** - Don't hardcode credentials
3. **Limit file permissions** - `chmod 600 .env`
4. **Keep dependencies updated** - Regular `pip install --upgrade`
5. **Use Docker for isolation** - Prevents host system exposure
6. **Set resource limits** - Configured in `docker-compose.yml`

---

## Summary

| Method | Complexity | Isolation | Best For |
|--------|-----------|-----------|----------|
| Direct Python | ⭐ | ⭐ | Quick testing |
| Startup Script | ⭐⭐ | ⭐⭐ | Local development |
| Docker | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Portable deployment |
| Docker Compose | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Production use |

**Recommended**: Use **Docker Compose** for production, **Startup Script** for development.
