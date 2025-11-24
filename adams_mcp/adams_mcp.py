import os
import platform
import logging
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from PyPDF2 import PdfReader
from adams_client_v4 import AdamsClient

# Optional env loader
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logging.info("Starting ADAMS MCP Server")

from threading import Lock
import time

class SimpleRateLimiter:
    def __init__(self, calls_per_minute=30):
        self.calls_per_minute = calls_per_minute
        self.interval = 60.0 / calls_per_minute
        self.lock = Lock()
        self.last_call = 0.0
    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                delay = self.interval - elapsed
                logging.info(f"Rate limiting active — waiting {delay:.2f}s")
                time.sleep(delay)
            self.last_call = time.time()

rate_limiter = SimpleRateLimiter(calls_per_minute=20)

# ------------------------------------------------------------
# Initialize Client + MCP Engine
# ------------------------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

client = AdamsClient(
    google_api_key=GOOGLE_API_KEY,
    google_cx=GOOGLE_CX
)

mcp = FastMCP("ADAMS_MCP")

# ------------------------------------------------------------
# Utility — Relevance Scoring
# ------------------------------------------------------------
def score_relevance(query: str, title: str | None, doc_type: str | None):
    """Simple heuristic scoring for Claude to rank documents."""
    if not title:
        return 0

    q_low = query.lower()
    t_low = title.lower()

    score = 0

    if q_low in t_low:
        score += 5
    if any(word in t_low for word in q_low.split()):
        score += 2
    if doc_type and "safety" in doc_type.lower():
        score += 1
    if doc_type and "reactor" in doc_type.lower():
        score += 1

    return score

# ------------------------------------------------------------
# Tool: Search ADAMS (Hybrid: ADAMS + Google)
# ------------------------------------------------------------
@mcp.tool()
async def search_adams(
    query: str,
    top_n: int = 5,
    max_pages: int = 1,
    use_google: bool = True
):
    """
    Search NRC ADAMS (and optionally Google) for documents.

    • Expands query using synonyms (MSR → molten salt, etc.)
    • Uses caching (5 minutes)
    • Adds relevance scoring
    • Returns rationale for each item
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    try:
        # Step 1 — ADAMS search with synonym expansion
        rate_limiter.wait()
        adams_results = client.search(query, max_pages=max_pages)

        enriched_adams = []
        for doc in adams_results:
            enriched_adams.append({
                "title": doc.title,
                "accession_number": doc.accession_number,
                "source": "ADAMS",
                "document_type": doc.document_type,
                "score": score_relevance(query, doc.title, doc.document_type),
                "rationale": f"Matched ADAMS index (document_type={doc.document_type})"
            })

        # Step 2 — Optional Google
        google_results = []
        if use_google:
            rate_limiter.wait()
            g_results = client.google_search(f"site:pbadupws.nrc.gov {query}", num=top_n)
            for item in g_results:
                google_results.append({
                    "title": item["title"],
                    "link": item["link"],
                    "snippet": item["snippet"],
                    "source": "Google",
                    "score": score_relevance(query, item["title"], None),
                    "rationale": "Google match for NRC domain"
                })

        # Step 3 — Combine + sort
        combined = enriched_adams + google_results
        combined.sort(key=lambda r: r["score"], reverse=True)

        return {"results": combined[:top_n]}

    except Exception as e:
        logging.error(f"search_adams error: {e}")
        return {"error": str(e)}

# ------------------------------------------------------------
# Helper — Get OS Downloads Folder
# ------------------------------------------------------------
def get_system_downloads_folder():
    """Returns the REAL OS downloads folder."""
    home = Path.home()

    # Windows & macOS & Linux default Downloads directory
    downloads = home / "Downloads"

    # Create ADAMS subfolder
    adams_folder = downloads / "ADAMS"
    adams_folder.mkdir(parents=True, exist_ok=True)

    return adams_folder

# ------------------------------------------------------------
# Tool: Download a Single ADAMS Document (NEW VERSION)
# ------------------------------------------------------------
@mcp.tool()
async def download_adams(accession_number: str):
    """
    Download an ADAMS document directly into the user's real OS Downloads folder.

    Example:
        C:/Users/Name/Downloads/ADAMS/ML12345A678.pdf
    """
    if not accession_number or not accession_number.strip():
        return {"error": "Accession number required"}

    try:
        # Build direct PDF URL (no searching needed)
        if accession_number.startswith("ML") and len(accession_number) >= 10:
            folder = accession_number[:6]
            url = f"https://pbadupws.nrc.gov/docs/{folder}/{accession_number}.pdf"
        else:
            return {"error": f"Accession number '{accession_number}' is not valid"}

        # Get system Downloads/ADAMS folder
        download_dir = get_system_downloads_folder()

        # Output path
        file_path = download_dir / f"{accession_number}.pdf"

        logging.info(f"Downloading {accession_number} → {file_path}")

        # Perform the download
        import requests
        resp = requests.get(url, stream=True, timeout=20)

        if resp.status_code != 200:
            return {
                "error": f"Download failed (status {resp.status_code})",
                "url": url
            }

        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return {
            "message": "Download successful",
            "accession_number": accession_number,
            "path": str(file_path),
            "folder": str(download_dir),
            "url": url
        }

    except Exception as e:
        logging.error(f"download_adams error: {e}")
        return {"error": str(e)}

# ------------------------------------------------------------
# Tool: Batch Download Many ADAMS Documents (New Version)
# ------------------------------------------------------------
@mcp.tool()
async def download_adams_batch(accession_numbers: list[str]):
    """
    Batch-download multiple ADAMS documents.

    • Saves all PDFs into user's real OS Downloads/ADAMS folder
    • Returns per-file success or error
    """
    if not accession_numbers or not isinstance(accession_numbers, list):
        return {"error": "You must provide a list of accession numbers"}

    # Get the real downloads folder
    download_dir = get_system_downloads_folder()

    results = []
    for acc in accession_numbers:
        acc = acc.strip()

        if not acc or not acc.startswith("ML") or len(acc) < 10:
            results.append({
                "accession_number": acc,
                "status": "failed",
                "reason": "Invalid accession number format"
            })
            continue

        try:
            # Build NRC direct PDF URL
            folder = acc[:6]
            url = f"https://pbadupws.nrc.gov/docs/{folder}/{acc}.pdf"

            file_path = download_dir / f"{acc}.pdf"

            logging.info(f"[Batch] Downloading {acc} → {file_path}")

            import requests
            resp = requests.get(url, stream=True, timeout=20)

            if resp.status_code != 200:
                results.append({
                    "accession_number": acc,
                    "status": "failed",
                    "reason": f"HTTP {resp.status_code}",
                    "url": url
                })
                continue

            with open(file_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            results.append({
                "accession_number": acc,
                "status": "success",
                "path": str(file_path),
                "url": url
            })

        except Exception as e:
            logging.error(f"Batch download error for {acc}: {e}")
            results.append({
                "accession_number": acc,
                "status": "failed",
                "reason": str(e)
            })

    return {
        "folder": str(download_dir),
        "results": results
    }

# ------------------------------------------------------------
# Tool: Summarize PDF
# ------------------------------------------------------------
@mcp.tool()
async def summarize_pdf(
    path: str,
    max_chars: int = 2000
):
    """
    Extract text from a PDF for LLM summarization.
    """
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}

    try:
        reader = PdfReader(path)
        text = " ".join((p.extract_text() or "") for p in reader.pages)
        text = " ".join(text.split())  # clean whitespace

        result = text[:max_chars] + ("..." if len(text) > max_chars else "")

        return {
            "text": result,
            "total_pages": len(reader.pages),
            "total_chars": len(text),
            "extracted": len(result)
        }

    except Exception as e:
        logging.error(f"summarize_pdf error: {e}")
        return {"error": str(e)}

# ------------------------------------------------------------
# Run MCP Server
# ------------------------------------------------------------
if __name__ == "__main__":
    logging.info("ADAMS MCP Server v2 is running...")
    mcp.run()

