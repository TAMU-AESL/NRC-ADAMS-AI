#!/bin/bash
# ADAMS MCP Server Startup Script

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "  ADAMS MCP Server Startup"
echo "========================================="

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python3 is not installed${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo -e "${GREEN}✓${NC} Python version: $PYTHON_VERSION"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Creating...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Check for .env file
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Warning: .env file not found${NC}"
    echo "Google search will be disabled unless you:"
    echo "  1. Copy .env.example to .env"
    echo "  2. Add your GOOGLE_API_KEY and GOOGLE_CX"
    echo ""
fi

# Create necessary directories
mkdir -p logs downloads

# Display configuration
echo ""
echo "Configuration:"
echo "  - Logs: ./logs/mcp_server.log"
echo "  - Downloads: ./downloads/"

if [ -f ".env" ]; then
    if grep -q "GOOGLE_API_KEY=your_google_api_key_here" .env; then
        echo -e "  - Google Search: ${YELLOW}Not configured${NC}"
    else
        echo -e "  - Google Search: ${GREEN}Configured${NC}"
    fi
else
    echo -e "  - Google Search: ${YELLOW}Not configured${NC}"
fi

echo ""
echo -e "${GREEN}Starting ADAMS MCP Server...${NC}"
echo "Press Ctrl+C to stop"
echo ""

# Start the server
python3 adams_mcp.py
