# Testing Notes for ADAMS MCP Server

## Pre-Installation Tests

✓ **Syntax Validation**: All Python files have been syntax-checked and are valid.

## Installation Instructions

To fully test the MCP server, install the required dependencies:

```bash
pip install -r requirements.txt
```

## Running Tests

### Automated Test Suite

Run the comprehensive test suite:

```bash
python test_adams.py
```

This will test:
- Module imports
- Client initialization
- Input validation
- Document class functionality
- MCP server syntax
- Live ADAMS search (optional, requires network)

### Manual Testing

#### 1. Test AdamsClient directly

```python
from adams_client_v3 import AdamsClient

# Initialize client
client = AdamsClient()

# Test search
results = client.search(query="reactor safety", max_pages=1)
print(f"Found {len(results)} documents")

# Test download
if results:
    doc = results[0]
    print(f"Downloading: {doc.title}")
    path = doc.download(directory="./test_downloads")
    print(f"Saved to: {path}")
```

#### 2. Test MCP Server

Start the server:
```bash
python adams_mcp.py
```

The server will run and log to `mcp_server.log`. You can then connect to it using an MCP client.

#### 3. Test Individual Tools

```python
import asyncio
from adams_mcp import search_adams, download_adams, summarize_pdf

async def test_tools():
    # Test search
    result = await search_adams(
        query="inspection report",
        max_pages=1,
        top_n=5,
        use_google=False
    )
    print("Search results:", result)

    # Test download (use a real accession number)
    result = await download_adams(
        accession_number="ML12345A678",
        directory="./test_downloads"
    )
    print("Download result:", result)

    # Test PDF extraction
    result = await summarize_pdf(
        path="./test_downloads/ML12345A678.pdf",
        max_chars=1000
    )
    print("PDF extraction result:", result)

asyncio.run(test_tools())
```

## Validation Checks Performed

### Input Validation

✓ Query validation (empty strings, length limits)
✓ Parameter bounds (max_pages: 1-10, top_n: 1-50)
✓ Accession number format checking
✓ File path validation
✓ PDF file existence checks

### Error Handling

✓ Network request failures with retry logic
✓ XML parsing errors
✓ File I/O errors
✓ Missing API credentials
✓ Invalid search criteria

### Security

✓ Hardcoded credentials removed
✓ Environment variable support
✓ .env file loading
✓ Sensitive files in .gitignore

## Expected Test Results

When all dependencies are installed:

1. **test_imports**: Should pass - all required modules import successfully
2. **test_client_initialization**: Should pass - AdamsClient initializes correctly
3. **test_input_validation**: Should pass - validation rules work correctly
4. **test_document_class**: Should pass - AdamsDocument methods work
5. **test_mcp_server_syntax**: Should pass - MCP server file is valid Python
6. **test_live_search**: May vary - depends on network and ADAMS API availability

## Known Limitations

1. **Google Search**: Requires valid API credentials in environment
2. **Network Dependency**: Live tests require internet connection and ADAMS API availability
3. **PDF Download**: Requires valid accession numbers
4. **Rate Limiting**: Google API has rate limits (100 queries/day for free tier)

## Troubleshooting

### Import Errors
- Ensure all dependencies are installed: `pip install -r requirements.txt`

### Google Search Not Working
- Check that GOOGLE_API_KEY and GOOGLE_CX are set in environment or .env file
- Verify API key is active and has quota available

### ADAMS Search Fails
- Check network connectivity
- Verify ADAMS API is accessible: https://adams.nrc.gov/wba/
- Check logs in `mcp_server.log` for details

### PDF Download Fails
- Verify the accession number is correct and exists
- Check that the document is publicly available
- Ensure write permissions to download directory

## Code Quality Checks

✓ All functions have docstrings
✓ Input validation on all public methods
✓ Comprehensive error messages
✓ Logging throughout
✓ No hardcoded secrets
✓ Proper exception handling
