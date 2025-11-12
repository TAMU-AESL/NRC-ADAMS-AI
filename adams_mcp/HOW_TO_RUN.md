# How to Run ADAMS MCP Server - Quick Reference

## Understanding How It Works

**IMPORTANT**: MCP servers are **not standalone web services**. They are tools that run alongside AI assistants.

### The MCP Model

```
┌─────────────────┐         ┌──────────────────┐
│  AI Assistant   │ stdio   │   ADAMS MCP      │
│ (Claude Desktop)├────────►│     Server       │
│                 │◄────────┤   (Python)       │
└─────────────────┘         └──────────────────┘
                                     │
                                     ▼
                            ┌──────────────────┐
                            │   NRC ADAMS      │
                            │     API          │
                            └──────────────────┘
```

The AI assistant **starts and manages** the MCP server. You don't run it as a background service.

---

## 🚀 Four Ways to Run

### 1️⃣ Direct Python (Fastest Setup)

```bash
cd /workspace/projects/adams_mcp
pip install -r requirements.txt
python adams_mcp.py
```

**When to use**: Quick testing, simple environments

---

### 2️⃣ Startup Script (Best for Dev)

```bash
cd /workspace/projects/adams_mcp
./start_server.sh
```

**When to use**: Local development, automatic venv setup

**What it does**:
- Creates virtual environment
- Installs dependencies
- Starts server with nice output

---

### 3️⃣ Docker (Most Portable)

```bash
cd /workspace/projects/adams_mcp

# Build image
docker build -t adams-mcp:latest .

# Run with environment file
docker run -d \
  --name adams-mcp \
  --env-file .env \
  -v $(pwd)/downloads:/app/downloads \
  -v $(pwd)/logs:/app/logs \
  adams-mcp:latest

# View logs
docker logs -f adams-mcp
```

**When to use**: Need isolation, different machines, want consistency

---

### 4️⃣ Docker Compose (Production Ready)

```bash
cd /workspace/projects/adams_mcp

# Copy environment template
cp .env.example .env
nano .env  # Add your Google API keys if needed

# Start service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop service
docker-compose down
```

**When to use**: Production, persistent deployment, easy management

---

## ⚙️ Configuring with Claude Desktop

**After choosing a run method**, configure Claude Desktop:

### Location of config file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/claude/claude_desktop_config.json`

### Example Configuration:

#### For Direct Python / Startup Script:
```json
{
  "mcpServers": {
    "adams": {
      "command": "python3",
      "args": ["/workspace/projects/adams_mcp/adams_mcp.py"],
      "env": {
        "GOOGLE_API_KEY": "your_key_here",
        "GOOGLE_CX": "your_cx_here"
      }
    }
  }
}
```

#### For Docker:
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

**Change `/workspace/projects/adams_mcp` to your actual path!**

---

## 🧪 Testing Your Setup

### Test 1: Run test suite
```bash
python test_adams.py
```

### Test 2: Check imports
```bash
python -c "from adams_client_v3 import AdamsClient; print('✓ OK')"
```

### Test 3: Try a search
```bash
python -c "
from adams_client_v3 import AdamsClient
client = AdamsClient()
results = client.search(query='safety', max_pages=1)
print(f'Found {len(results)} documents')
"
```

---

## 📋 Environment Setup (Optional)

Google search is **optional**. Without it, you still get ADAMS search.

```bash
# Create .env file
cat > .env << EOF
GOOGLE_API_KEY=your_api_key_here
GOOGLE_CX=your_custom_search_id_here
EOF

# Protect it
chmod 600 .env
```

Get credentials: https://developers.google.com/custom-search/v1/overview

---

## 🆘 Common Issues

### "Module not found"
```bash
pip install -r requirements.txt
```

### "Permission denied: start_server.sh"
```bash
chmod +x start_server.sh
```

### "Docker: command not found"
Install Docker: https://docs.docker.com/get-docker/

### "MCP server not working in Claude"
1. Check the path in `claude_desktop_config.json`
2. Restart Claude Desktop
3. Check logs: `cat logs/mcp_server.log`

---

## 📚 Documentation Quick Links

- **Full README**: [README.md](README.md)
- **Quick Start**: [QUICKSTART.md](QUICKSTART.md)
- **Deployment Guide**: [DEPLOYMENT.md](DEPLOYMENT.md) ⭐ **Most detailed**
- **Testing**: [TESTING_NOTES.md](TESTING_NOTES.md)
- **Changes Made**: [IMPROVEMENTS_SUMMARY.md](IMPROVEMENTS_SUMMARY.md)

---

## 🎯 Recommended Setup by Use Case

| Use Case | Recommended Method | Why |
|----------|-------------------|-----|
| First time trying | Direct Python | Fastest to test |
| Daily development | Startup Script | Auto-setup, isolated |
| Multiple machines | Docker | Consistent everywhere |
| Production/server | Docker Compose | Reliable, monitored |
| CI/CD pipeline | Docker | Reproducible builds |

---

## 💡 Pro Tips

1. **Use Docker Compose for production** - It handles restarts, logging, and resource limits
2. **Don't commit .env** - It's in .gitignore for a reason
3. **Check logs first** - `logs/mcp_server.log` has the answers
4. **Google search is optional** - ADAMS search works without it
5. **Test before configuring Claude** - Run `python test_adams.py` first

---

## Summary

```bash
# Quickest way to get started:
./start_server.sh

# Most robust way:
docker-compose up -d

# Then configure Claude Desktop with the path to your server
```

**Remember**: The MCP server runs when Claude needs it, not as a standalone service!
