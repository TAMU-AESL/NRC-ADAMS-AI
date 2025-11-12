# Quick Start Guide - ADAMS MCP Server

Get up and running with the ADAMS MCP Server in 5 minutes.

## Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 2: (Optional) Configure Google Search

If you want to use hybrid ADAMS + Google search:

```bash
# Copy the environment template
cp .env.example .env

# Edit .env and add your credentials
# GOOGLE_API_KEY=your_actual_api_key
# GOOGLE_CX=your_actual_search_engine_id
```

Get your Google API credentials:
- Visit: https://developers.google.com/custom-search/v1/overview
- Create a Custom Search Engine and API key

## Step 3: Test the Installation

```bash
python test_adams.py
```

You should see all tests pass.

## Step 4: Start the MCP Server

```bash
python adams_mcp.py
```

The server will log to `mcp_server.log`.

## Step 5: Try a Simple Search

Create a test file `example.py`:

```python
from adams_client_v3 import AdamsClient

# Initialize the client
client = AdamsClient()

# Search for documents
print("Searching ADAMS...")
results = client.search(
    query="reactor safety analysis",
    max_pages=1
)

print(f"\nFound {len(results)} documents:")
for i, doc in enumerate(results[:5], 1):
    print(f"{i}. {doc.title}")
    print(f"   Accession: {doc.accession_number}")
    print(f"   Date: {doc.document_date}")
    print()

# Download the first document
if results:
    print(f"Downloading first document...")
    path = results[0].download(directory="./downloads")
    print(f"Saved to: {path}")
```

Run it:
```bash
python example.py
```

## Common Use Cases

### Search for specific document type

```python
client = AdamsClient()
results = client.search(
    query="inspection",
    filters={"DocumentType": "Inspection Report"},
    max_pages=2
)
```

### Download by accession number

```python
results = client.search(
    filters={"AccessionNumber": "ML12345A678"}
)
if results:
    path = results[0].download(directory="./my_docs")
```

### Get recent documents

```python
results = client.search(
    added_this_month=True,
    max_pages=1
)
```

### Batch download

```python
results = client.search(query="emergency preparedness", max_pages=3)
paths = client.download_all(results, directory="./batch_download")
print(f"Downloaded {len(paths)} files")
```

## Troubleshooting

**Problem**: `ModuleNotFoundError: No module named 'requests'`
**Solution**: Run `pip install -r requirements.txt`

**Problem**: Google search not working
**Solution**: Check `.env` file has valid GOOGLE_API_KEY and GOOGLE_CX

**Problem**: No results returned
**Solution**: Check network connection and try a broader search term

**Problem**: Download fails
**Solution**: Verify the accession number exists and is publicly available

## Next Steps

- Read the full [README.md](README.md) for detailed documentation
- Check [TESTING_NOTES.md](TESTING_NOTES.md) for testing information
- Review the code in `adams_client_v3.py` for advanced usage

## Getting Help

- Check the logs: `cat mcp_server.log`
- Review error messages (all tools return descriptive errors)
- Verify your environment variables are set correctly

## Example MCP Usage

If you're using this as an MCP server with an AI assistant:

```
# Example prompts to try:

"Search ADAMS for recent inspection reports at nuclear power plants"

"Download document ML12345A678 and extract the text"

"Find all documents related to emergency preparedness from this month"

"Search for reactor safety analyses and summarize the first result"
```

Happy searching!
