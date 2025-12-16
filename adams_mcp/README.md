# ADAMS MCP Server

A Model Context Protocol (MCP) server that provides LLMs and AI agents with tools to search, download, and extract text from documents in the NRC ADAMS (Agencywide Documents Access and Management System) repository.
This is something that I am continuously working to optimize to provide better results.
## Features

- **Hybrid Search**: Search NRC ADAMS database with optional Google Custom Search supplementation
- **Document Download**: Download documents by accession number
- **PDF Text Extraction**: Extract text content from PDF files for LLM processing
- **Robust Error Handling**: Comprehensive input validation and error messages
- **Retry Logic**: Built-in retry mechanism for API requests

## Installation

### Prerequisites

- Python 3.8 or higher
- pip package manager

### Setup

1. Clone or download this repository

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. (Optional) Configure Google Custom Search API for supplementary search:
   - Copy `.env.example` to `.env`:
     ```bash
     cp .env.example .env
     ```
   - Add your Google API credentials to `.env`:
     ```
     GOOGLE_API_KEY=your_api_key_here
     GOOGLE_CX=your_custom_search_engine_id_here
     ```
   - Get credentials from: https://developers.google.com/custom-search/v1/overview

## Deployment Options

The ADAMS MCP server can be run in multiple ways for maximum portability:

### Quick Start (Direct Python)
```bash
python adams_mcp.py
```

### With Startup Script (Recommended for Development)
```bash
./start_server.sh
```

### Docker Container
```bash
docker build -t adams-mcp:latest .
docker run -d --name adams-mcp --env-file .env adams-mcp:latest
```

### Docker Compose (Recommended for Production)
```bash
docker-compose up -d
```

**📖 For detailed deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md)**

This includes:
- Configuration for Claude Desktop
- Production deployment checklist
- Troubleshooting guide
- All deployment methods explained

## Usage

### Starting the MCP Server

```bash
python adams_mcp.py
```

The server will log to `mcp_server.log` in the current directory.

### Available Tools

#### 1. `search_adams`

Search the NRC ADAMS database for nuclear regulatory documents.

**Parameters:**
- `query` (required): Search keywords (max 500 characters)
- `max_pages` (optional): Number of ADAMS result pages to fetch (1-10, default: 1)
- `top_n` (optional): Number of top results to return (1-50, default: 5)
- `use_google` (optional): Whether to supplement with Google search (default: true)

**Returns:**
- Dictionary with `results` list containing document metadata (title, accession number, date, type, etc.)

**Example:**
```python
{
  "query": "reactor safety analysis",
  "max_pages": 2,
  "top_n": 10,
  "use_google": true
}
```

#### 2. `download_adams`

Download a document from NRC ADAMS by its accession number.

**Parameters:**
- `accession_number` (required): NRC accession number (e.g., "ML12345A678")
- `directory` (optional): Download directory (default: "./downloads")

**Returns:**
- Dictionary with `path` to the downloaded file

**Example:**
```python
{
  "accession_number": "ML12345A678",
  "directory": "./my_downloads"
}
```

#### 3. `summarize_pdf`

Extract text content from a downloaded PDF file.

**Parameters:**
- `path` (required): File path to the PDF
- `max_chars` (optional): Maximum characters to extract (100-10000, default: 2000)

**Returns:**
- Dictionary with:
  - `text`: Extracted text content
  - `total_pages`: Number of pages in PDF
  - `total_chars`: Total characters in PDF
  - `extracted_chars`: Number of characters extracted

**Example:**
```python
{
  "path": "./downloads/ML12345A678.pdf",
  "max_chars": 5000
}
```

## Using the Python API Directly

The `adams_client_v3.py` module can also be used as a standalone Python library:

```python
from adams_client_v3 import AdamsClient

# Initialize client
client = AdamsClient()

# Search for documents
results = client.search(
    query="emergency preparedness",
    max_pages=2
)

# Download a document
for doc in results[:3]:
    file_path = doc.download(directory="./my_pdfs")
    print(f"Downloaded: {file_path}")

# Save search results to JSON
client.save_results_to_json(results, "search_results.json")
```

### Advanced Search Options

```python
# Search with filters
results = client.search(
    query="inspection report",
    filters={
        "DocumentType": "Inspection Report",
        "DocketNumber": "05000456"
    },
    max_pages=3
)

# Search recent documents
results = client.search(
    added_this_month=True,
    max_pages=1
)

# Search by accession number
results = client.search(
    filters={"AccessionNumber": "ML12345A678"}
)
```

## Configuration

### Environment Variables

- `GOOGLE_API_KEY`: Google Custom Search API key (optional)
- `GOOGLE_CX`: Google Custom Search Engine ID (optional)

### Logging

The MCP server logs to `mcp_server.log`. You can adjust the logging level in `adams_mcp.py`:

```python
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,  # Change to DEBUG for more verbose logging
    format="%(asctime)s [%(levelname)s] %(message)s"
)
```

## Error Handling

All tools return error information in a consistent format:

```json
{
  "error": "Description of what went wrong"
}
```

Common errors:
- Empty or invalid query strings
- Out-of-bounds parameter values
- Network/API failures
- File not found errors
- Invalid accession numbers

## Project Structure

```
adams_mcp/
├── adams_mcp.py           # MCP server implementation
├── adams_client_v3.py     # ADAMS API client library
├── server.json            # MCP server configuration
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variable template
├── .gitignore            # Git ignore rules
└── README.md             # This file
```

## Development

### Running Tests

(Tests to be implemented)

```bash
pytest tests/
```

### Contributing

1. Follow PEP 8 style guidelines
2. Add docstrings to all functions and classes
3. Include input validation for all public methods
4. Update tests for new features

## License

(Add your license here)

## About NRC ADAMS

The NRC's Agencywide Documents Access and Management System (ADAMS) provides public access to documents related to nuclear power plants, fuel cycle facilities, and radioactive materials. This includes inspection reports, license applications, correspondence, and more.

- ADAMS Public Website: https://www.nrc.gov/reading-rm/adams.html
- ADAMS Web Interface: https://adams.nrc.gov/wba/

## Support

For issues or questions:
- Check the logs in `mcp_server.log`
- Review error messages returned by the tools
- Ensure all dependencies are installed correctly
- Verify environment variables are set if using Google search

## Version History

- **0.1.0** (Current)
  - Initial release
  - Search, download, and PDF text extraction tools
  - Hybrid ADAMS + Google search
  - Input validation and error handling
