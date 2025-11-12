import os
import logging
from mcp.server.fastmcp import FastMCP
from adams_client_v3 import AdamsClient
from PyPDF2 import PdfReader

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, will use system env vars only

# ─────────────────────────────────────────────
# Logging Setup — writes to file, not stdout
# ─────────────────────────────────────────────
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
SERVER_VERSION = "0.1.0"
logging.info("ADAMS MCP server initializing...")

# ─────────────────────────────────────────────
# Initialize ADAMS client and MCP server
# ─────────────────────────────────────────────
# Load Google API credentials from environment variables
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

client = AdamsClient(
    google_api_key=GOOGLE_API_KEY,
    google_cx=GOOGLE_CX
)

mcp = FastMCP("ADAMS_MCP")

# ─────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────
@mcp.tool()
async def search_adams(query: str, max_pages: int = 1, top_n: int = 5, use_google: bool = True):
    """Search NRC ADAMS and Google for nuclear-related documents by keyword.
    Combines both sources for richer, hybrid research results.

    Args:
        query: Search keywords (must be non-empty, max 500 characters)
        max_pages: Number of result pages to fetch from ADAMS (1-10)
        top_n: Number of top results to return (1-50)
        use_google: Whether to supplement with Google search results
    """
    try:
        # Input validation
        if not query or not query.strip():
            return {"error": "Query cannot be empty"}
        if len(query) > 500:
            return {"error": "Query exceeds maximum length of 500 characters"}
        if max_pages < 1 or max_pages > 10:
            return {"error": "max_pages must be between 1 and 10"}
        if top_n < 1 or top_n > 50:
            return {"error": "top_n must be between 1 and 50"}
        adams_results = []
        google_results = []

        # ─────────────────────────────────────────────
        # 1. Try NRC ADAMS Search
        # ─────────────────────────────────────────────
        try:
            adams_results = client.search(query=query, max_pages=max_pages)
            adams_results.sort(key=lambda d: (d.added_date or "", len(d.title or "")), reverse=True)
            adams_top = [
                {
                    "title": d.title,
                    "accession_number": d.accession_number,
                    "date": d.document_date,
                    "source": "ADAMS",
                    "document_type": d.document_type,
                }
                for d in adams_results[:top_n]
            ]
        except Exception as e:
            logging.warning(f"ADAMS search failed: {e}")
            adams_top = []

        # ─────────────────────────────────────────────
        # 2. Google Fallback or Supplementary Search
        # ─────────────────────────────────────────────
        if use_google and client.google_api_key and client.google_cx:
            try:
                google_results = client.google_search(f"site:pbadupws.nrc.gov {query}", num=top_n)
                for item in google_results:
                    item["source"] = "Google"
            except Exception as g_err:
                logging.warning(f"Google search failed: {g_err}")
                google_results = []

        # ─────────────────────────────────────────────
        # 3. Merge and Deduplicate Results
        # ─────────────────────────────────────────────
        seen_titles = set()
        combined = []
        for r in adams_top + google_results:
            title = r.get("title")
            if title and title not in seen_titles:
                seen_titles.add(title)
                combined.append(r)

        logging.info(f"search_adams: query='{query}', combined_results={len(combined)}")
        return {"results": combined}

    except Exception as e:
        logging.error(f"search_adams error: {e}")
        return {"error": str(e)}


@mcp.tool()
async def download_adams(accession_number: str, directory: str = "./downloads"):
    """Download a document by NRC accession number.

    Args:
        accession_number: NRC accession number (e.g., 'ML12345A678')
        directory: Directory to save the downloaded file (default: './downloads')
    """
    try:
        # Input validation
        if not accession_number or not accession_number.strip():
            return {"error": "Accession number cannot be empty"}
        if not accession_number.startswith("ML") or len(accession_number) != 11:
            logging.warning(f"Accession number '{accession_number}' may not be in standard ML format")
        os.makedirs(directory, exist_ok=True)
        docs = client.search(filters={"AccessionNumber": accession_number})
        if not docs:
            msg = f"No document found for accession {accession_number}"
            logging.warning(msg)
            return {"error": msg}
        path = docs[0].download(directory=directory)
        logging.info(f"Downloaded {accession_number} to {path}")
        return {"path": path}
    except Exception as e:
        logging.error(f"download_adams error: {e}")
        return {"error": str(e)}


@mcp.tool()
async def summarize_pdf(path: str, max_chars: int = 2000):
    """Extract and return text content from a downloaded PDF file from ADAMS.

    Args:
        path: File path to the PDF file
        max_chars: Maximum number of characters to extract (default: 2000, max: 10000)

    Note: This extracts the first max_chars of text from the PDF. For actual
    summarization, the calling LLM should process this extracted text.
    """
    try:
        # Input validation
        if not path or not path.strip():
            return {"error": "Path cannot be empty"}
        if not os.path.exists(path):
            return {"error": f"File not found: {path}"}
        if not path.lower().endswith('.pdf'):
            return {"error": "File must be a PDF"}
        if max_chars < 100 or max_chars > 10000:
            return {"error": "max_chars must be between 100 and 10000"}

        reader = PdfReader(path)
        total_pages = len(reader.pages)
        text = " ".join([p.extract_text() or "" for p in reader.pages])

        # Clean up whitespace
        text = " ".join(text.split())

        extracted = text[:max_chars] + ("..." if len(text) > max_chars else "")
        logging.info(f"summarize_pdf: extracted {len(extracted)} chars from {path} ({total_pages} pages)")

        return {
            "text": extracted,
            "total_pages": total_pages,
            "total_chars": len(text),
            "extracted_chars": len(extracted)
        }
    except Exception as e:
        logging.error(f"summarize_pdf error: {e}")
        return {"error": str(e)}

# ─────────────────────────────────────────────
# Run the MCP Server
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logging.info("✅ Starting ADAMS MCP server with Google hybrid search...")
    mcp.run()
