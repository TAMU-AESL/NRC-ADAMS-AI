import os
import platform
import logging
import re
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
print("Loaded Google key:", GOOGLE_API_KEY)
print("Loaded CX:", GOOGLE_CX)

mcp = FastMCP("ADAMS_MCP")

# ------------------------------------------------------------
# Utility — Relevance Scoring
# ------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    """Tokenize a string into a set of alphanumeric lowercase tokens."""
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))

def ngram_score(query_tokens: set[str], title_tokens: set[str]) -> float:
    """
    Give extra weight to multi-word phrase (bigram) matches between
    the query and the title.
    """
    score = 0.0
    title_str = " ".join(title_tokens)
    query_list = list(query_tokens)

    for i in range(len(query_list) - 1):
        bigram = f"{query_list[i]} {query_list[i+1]}"
        if bigram in title_str:
            score += 3.0

    return score

def score_relevance(query: str, title: str | None, doc_type: str | None):
    """
    Improved relevance scoring combining:
      • token overlap (Jaccard-weighted)
      • exact phrase inclusion
      • bigram-based n-gram scoring
      • domain-specific boosts on document_type
    """
    if not title:
        return 0.0

    query_tokens = tokenize(query)
    title_tokens = tokenize(title)

    score = 0.0

    # Token overlap / Jaccard-style weighting
    overlap = query_tokens & title_tokens
    if overlap:
        jaccard = len(overlap) / max(len(query_tokens), 1)
        score += jaccard * 10.0

    # Exact phrase match
    if query.lower() in title.lower():
        score += 8.0

    # N-gram / bigram score
    score += ngram_score(query_tokens, title_tokens)

    # Domain-specific boosts
    if doc_type:
        dt_lower = doc_type.lower()
        if "reactor" in dt_lower:
            score += 2.0
        if "safety" in dt_lower:
            score += 1.0
        if "event" in dt_lower:
            score += 0.5
        if "inspection" in dt_lower:
            score += 1.5

    return round(score, 2)

# ------------------------------------------------------------
# Filtering & Sorting for Search Results
# ------------------------------------------------------------
def apply_filters(results: list[dict], filters: dict | None) -> list[dict]:
    """
    Apply user-specified filters to the combined result list.

    Supported filters keys:
      - "source": "ADAMS" or "Google"
      - "document_type": substring match on document_type
      - "title_contains": substring that must appear in title
      - "title_not_contains": substring that must NOT appear in title
      - "accession_startswith": prefix for accession_number (e.g., "ML23")
      - "min_score": minimum relevance score (inclusive)
      - "max_score": maximum relevance score (inclusive)
    """
    if not filters:
        return results

    filtered = []

    for r in results:
        ok = True

        # Filter: source
        if "source" in filters:
            if r.get("source", "").lower() != str(filters["source"]).lower():
                ok = False

        # Filter: document_type (substring match)
        if "document_type" in filters:
            dt = r.get("document_type", "") or ""
            if str(filters["document_type"]).lower() not in dt.lower():
                ok = False

        # Filter: title_contains
        if "title_contains" in filters:
            if str(filters["title_contains"]).lower() not in r.get("title", "").lower():
                ok = False

        # Filter: title_not_contains
        if "title_not_contains" in filters:
            if str(filters["title_not_contains"]).lower() in r.get("title", "").lower():
                ok = False

        # Filter: accession_startswith
        if "accession_startswith" in filters:
            acc = r.get("accession_number", "") or ""
            if not acc.startswith(str(filters["accession_startswith"])):
                ok = False

        # Filter: min_score
        if "min_score" in filters:
            try:
                if r.get("score", 0) < float(filters["min_score"]):
                    ok = False
            except (TypeError, ValueError):
                pass

        # Filter: max_score
        if "max_score" in filters:
            try:
                if r.get("score", 0) > float(filters["max_score"]):
                    ok = False
            except (TypeError, ValueError):
                pass

        if ok:
            filtered.append(r)

    return filtered


def sort_results(
    results: list[dict],
    sort_by: str = "score",
    descending: bool = True
) -> list[dict]:
    """
    Sort the results list by a given key.

    Allowed sort_by values (for now):
      - "score" (default)
      - "title"
      - "accession_number"
      - "source"
      - "document_type"

    Fallback: if the chosen sort_by key doesn't exist, we fall back to "score".
    """
    if not results:
        return results

    # If the desired sort key isn't present, fall back to "score"
    candidate_key = sort_by
    if candidate_key not in results[0]:
        candidate_key = "score"

    def key_fn(r: dict):
        val = r.get(candidate_key, "")
        # Normalize strings to lowercase for consistent sorting
        if isinstance(val, str):
            return val.lower()
        return val

    try:
        return sorted(results, key=key_fn, reverse=descending)
    except Exception as e:
        logging.error(f"sort_results error (sort_by={sort_by}): {e}")
        return results

# ------------------------------------------------------------
# Tool: Search ADAMS (Hybrid: ADAMS + Google)
# ------------------------------------------------------------
@mcp.tool()
async def search_adams(
    query: str,
    top_n: int = 5,
    max_pages: int = 1,
    use_google: bool = True,
    filters: dict | None = None,
    sort_by: str = "score",
    sort_desc: bool = True
):
    """
    Search NRC ADAMS (and optionally Google) for documents.

    • Expands query using synonyms (MSR → molten salt, etc.) via AdamsClient
    • Uses rate limiting
    • Adds relevance scoring
    • Supports filtering and sorting
    • Returns rationale for each item

    Parameters
    ----------
    query : str
        Search query.
    top_n : int
        Maximum number of items to return after filtering + sorting.
    max_pages : int
        Maximum ADAMS pages to search via AdamsClient.
    use_google : bool
        Whether to also search Google (NRC domain only).
    filters : dict | None
        Optional filter dictionary. Supported keys:
          - "source": "ADAMS" or "Google"
          - "document_type": substring match on document_type
          - "title_contains": substring that must appear in title
          - "title_not_contains": substring that must NOT appear in title
          - "accession_startswith": prefix for accession_number (e.g., "ML23")
          - "min_score": minimum relevance score (inclusive)
          - "max_score": maximum relevance score (inclusive)
    sort_by : str
        Field to sort by. Options:
          - "score" (default)
          - "title"
          - "accession_number"
          - "source"
          - "document_type"
    sort_desc : bool
        Sort in descending order (True) or ascending (False).
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}

    try:
        # Step 1 — ADAMS search with synonym expansion
        rate_limiter.wait()
        adams_results = client.search(query, max_pages=max_pages)

        enriched_adams: list[dict] = []
        for doc in adams_results:
            enriched_adams.append({
                "title": doc.title,
                "accession_number": doc.accession_number,
                "source": "ADAMS",
                "document_type": getattr(doc, "document_type", None),
                "score": score_relevance(query, doc.title, getattr(doc, "document_type", None)),
                "rationale": f"Matched ADAMS index (document_type={getattr(doc, 'document_type', None)})"
            })

        # Step 2 — Optional Google
        google_results: list[dict] = []
        if use_google:
            rate_limiter.wait()
            g_results = client.google_search(f"site:pbadupws.nrc.gov {query}", num=top_n)
            for item in g_results:
                google_results.append({
                    "title": item.get("title"),
                    "link": item.get("link"),
                    "snippet": item.get("snippet"),
                    "source": "Google",
                    "score": score_relevance(query, item.get("title"), None),
                    "rationale": "Google match for NRC domain"
                })

        # Step 3 — Combine
        combined = enriched_adams + google_results

        # Step 4 — Apply filters (if any)
        combined = apply_filters(combined, filters)

        # Step 5 — Sort results
        if combined:
            combined = sort_results(combined, sort_by=sort_by, descending=sort_desc)

        # Step 6 — Return the top_n results
        return {"results": combined[:top_n]}

    except Exception as e:
        logging.error(f"search_adams error: {e}")
        return {"error": str(e)}

def get_system_downloads_folder():
    """Returns the REAL OS downloads folder."""
    home = Path.home()

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
