import os
import logging
import re
import time
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, List, Any
from datetime import datetime

from dotenv import load_dotenv
from PyPDF2 import PdfReader
import requests

from mcp.server.fastmcp import FastMCP
from adams_client_v4 import AdamsClient

# Environment
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

# Logging Configuration
# ------------------------------------------------------------
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)

# Create separate loggers for different components
logger = logging.getLogger("ADAMS_MCP")
search_logger = logging.getLogger("ADAMS_MCP.search")
download_logger = logging.getLogger("ADAMS_MCP.download")
pdf_logger = logging.getLogger("ADAMS_MCP.pdf")

logger.info("Starting ADAMS MCP Server")

# Custom Exceptions
# ------------------------------------------------------------
class AdamsError(Exception):
    """Base exception for ADAMS operations"""
    pass

class InvalidAccessionError(AdamsError):
    """Invalid accession number format"""
    pass

class DownloadError(AdamsError):
    """Failed to download document"""
    pass

class PDFProcessingError(AdamsError):
    """Failed to process PDF"""
    pass

class SearchError(AdamsError):
    """Search operation failed"""
    pass

# Rate Limiter
# ------------------------------------------------------------
class SimpleRateLimiter:
    def __init__(self, calls_per_minute=20):
        self.interval = 60.0 / calls_per_minute
        self.lock = Lock()
        self.last_call = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_call = time.time()

rate_limiter = SimpleRateLimiter()

# Client + MCP
# ------------------------------------------------------------
client = AdamsClient(
    google_api_key=GOOGLE_API_KEY,
    google_cx=GOOGLE_CX
)

mcp = FastMCP("ADAMS_MCP")

# Validation Functions
# ------------------------------------------------------------
def validate_accession_number(accession: str) -> tuple[bool, Optional[str]]:
    """
    Validate accession number format.
    Returns (is_valid, error_message)
    """
    if not accession:
        return False, "Accession number cannot be empty"
    
    if not isinstance(accession, str):
        return False, "Accession number must be a string"
    
    if not accession.startswith("ML"):
        return False, "Accession number must start with 'ML'"
    
    if len(accession) < 8:
        return False, "Accession number is too short"
    
    # Check if it contains only valid characters (letters and numbers)
    if not re.match(r'^ML[A-Za-z0-9]+$', accession):
        return False, "Accession number contains invalid characters"
    
    return True, None

def validate_query(query: str) -> tuple[bool, Optional[str]]:
    """
    Validate search query.
    Returns (is_valid, error_message)
    """
    if not query or not query.strip():
        return False, "Query cannot be empty"
    
    if len(query.strip()) < 2:
        return False, "Query must be at least 2 characters"
    
    if len(query) > 500:
        return False, "Query is too long (max 500 characters)"
    
    return True, None

# Util — Tokenization & Relevance
# ------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    """Extract alphanumeric tokens from text."""
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))

def score_relevance(query: str, title: Optional[str], doc_type: Optional[str]) -> float:
    """
    Score relevance of a document to the search query.
    Returns a float score where higher is more relevant.
    """
    if not title:
        return 0.0

    q_tokens = tokenize(query)
    t_tokens = tokenize(title)

    overlap = q_tokens & t_tokens
    score = (len(overlap) / max(len(q_tokens), 1)) * 10.0

    if query.lower() in title.lower():
        score += 8.0

    if doc_type:
        dt = doc_type.lower()
        if "inspection" in dt:
            score += 2.0
        if "reactor" in dt:
            score += 1.5
        if "safety" in dt:
            score += 1.0

    return round(score, 2)

# Util - Deduplication
# ------------------------------------------------------------
def fingerprint_result(item: dict) -> str:
    """Create a unique fingerprint for a search result."""
    if item.get("accession_number"):
        return item["accession_number"]
    if item.get("link"):
        return item["link"].lower().strip()
    return (item.get("title") or "").lower().strip()

# Util — Robust PDF Fetch
# ------------------------------------------------------------
def fetch_pdf(url: str, retries: int = 3, timeout: int = 20) -> Optional[bytes]:
    """
    Fetch PDF from URL with retries and validation.
    Returns PDF bytes or None if failed.
    """
    for attempt in range(1, retries + 1):
        try:
            download_logger.info(f"Fetching PDF (attempt {attempt}/{retries}): {url}")
            
            response = requests.get(url, timeout=timeout, stream=True)
            response.raise_for_status()
            
            # Validate content type
            content_type = response.headers.get("Content-Type", "").lower()
            if "pdf" not in content_type:
                download_logger.warning(f"Non-PDF content type: {content_type}")
                return None
            
            # Check content length if available
            content_length = response.headers.get("Content-Length")
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                download_logger.info(f"PDF size: {size_mb:.2f} MB")
                
                if int(content_length) > 50_000_000:  # 50MB limit
                    download_logger.error(f"PDF too large: {size_mb:.2f} MB")
                    raise DownloadError(f"PDF exceeds size limit: {size_mb:.2f} MB")
            
            content = response.content
            download_logger.info(f"Successfully fetched PDF: {len(content)} bytes")
            return content
            
        except requests.exceptions.Timeout as e:
            download_logger.warning(f"Timeout on attempt {attempt}: {e}")
        except requests.exceptions.HTTPError as e:
            download_logger.error(f"HTTP error on attempt {attempt}: {e}")
            if response.status_code in [404, 403, 401]:
                # Don't retry for these errors
                return None
        except requests.exceptions.RequestException as e:
            download_logger.error(f"Request failed on attempt {attempt}: {e}")
        except Exception as e:
            download_logger.exception(f"Unexpected error on attempt {attempt}: {e}")
        
        if attempt < retries:
            wait_time = 2 ** attempt  # Exponential backoff
            download_logger.info(f"Waiting {wait_time}s before retry...")
            time.sleep(wait_time)
    
    download_logger.error(f"Failed to fetch PDF after {retries} attempts")
    return None

# Util - Chunked Text Collection
# ------------------------------------------------------------
def chunk_text(text: str, max_chars: int, chunk_size: int = 1200) -> str:
    """Extract text chunks up to max_chars."""
    chunks = []
    total = 0

    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break

    return " ".join(chunks)

# Downloads Folder
# ------------------------------------------------------------
def get_downloads_folder() -> Path:
    """Get or create the ADAMS downloads folder."""
    path = Path.home() / "Downloads" / "ADAMS"
    path.mkdir(parents=True, exist_ok=True)
    return path

# Filter Application
# ------------------------------------------------------------
def apply_filters(results: List[Dict[str, Any]], filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply filters to search results.
    
    Supported filters:
    - document_type: str or List[str] - Filter by document type(s)
    - min_score: float - Minimum relevance score
    - source: str - Filter by source (ADAMS or Google)
    - date_from: str - Filter documents from this date (YYYY-MM-DD)
    - date_to: str - Filter documents to this date (YYYY-MM-DD)
    """
    if not filters:
        return results
    
    filtered = results
    
    # Filter by document type
    if "document_type" in filters:
        doc_types = filters["document_type"]
        if isinstance(doc_types, str):
            doc_types = [doc_types]
        
        filtered = [
            r for r in filtered 
            if r.get("document_type") and r["document_type"] in doc_types
        ]
        search_logger.info(f"Filtered by document_type: {len(filtered)} results remain")
    
    # Filter by minimum score
    if "min_score" in filters:
        min_score = float(filters["min_score"])
        filtered = [r for r in filtered if r.get("score", 0) >= min_score]
        search_logger.info(f"Filtered by min_score {min_score}: {len(filtered)} results remain")
    
    # Filter by source
    if "source" in filters:
        source = filters["source"]
        filtered = [r for r in filtered if r.get("source") == source]
        search_logger.info(f"Filtered by source {source}: {len(filtered)} results remain")
    
    # Filter by date range (if date information is available)
    if "date_from" in filters or "date_to" in filters:
        date_filtered = []
        for r in filtered:
            doc_date = r.get("date") or r.get("document_date")
            if doc_date:
                try:
                    if isinstance(doc_date, str):
                        doc_date = datetime.fromisoformat(doc_date.split("T")[0])
                    
                    if "date_from" in filters:
                        date_from = datetime.fromisoformat(filters["date_from"])
                        if doc_date < date_from:
                            continue
                    
                    if "date_to" in filters:
                        date_to = datetime.fromisoformat(filters["date_to"])
                        if doc_date > date_to:
                            continue
                    
                    date_filtered.append(r)
                except (ValueError, TypeError) as e:
                    search_logger.warning(f"Invalid date format: {doc_date}, error: {e}")
                    continue
            else:
                # Keep results without dates if no strict date filtering
                date_filtered.append(r)
        
        filtered = date_filtered
        search_logger.info(f"Filtered by date range: {len(filtered)} results remain")
    
    return filtered

# Tool - Search_ADAMS
# ------------------------------------------------------------
@mcp.tool()
async def search_adams(
    query: str,
    top_n: int = 5,
    max_results: Optional[int] = None,
    max_pages: int = 1,
    use_google: bool = False,
    filters: Optional[Dict[str, Any]] = None,
    sort_by: str = "score",
    sort_desc: bool = True
) -> Dict[str, Any]:
    """
    Search NRC ADAMS database and optionally Google.
    
    Args:
        query: Search query string
        top_n: Number of results to return (default: 5)
        max_results: Override for top_n
        max_pages: Number of pages to search (default: 1)
        use_google: Whether to include Google search results (default: False)
        filters: Dictionary of filters to apply:
            - document_type: str or List[str] - Filter by document type(s)
            - min_score: float - Minimum relevance score
            - source: str - Filter by source (ADAMS or Google)
            - date_from: str - Filter documents from this date (YYYY-MM-DD)
            - date_to: str - Filter documents to this date (YYYY-MM-DD)
        sort_by: Field to sort by (default: "score")
        sort_desc: Sort in descending order (default: True)
    
    Returns:
        Dictionary with results or error
    """
    search_logger.info(f"Search request: query='{query}', top_n={top_n}, filters={filters}")
    
    # Validate query
    is_valid, error_msg = validate_query(query)
    if not is_valid:
        search_logger.error(f"Invalid query: {error_msg}")
        return {"error": error_msg, "query": query}

    if max_results is not None:
        top_n = max_results

    try:
        # Search ADAMS
        rate_limiter.wait()
        search_logger.info(f"Searching ADAMS with query: {query}")
        
        adams_docs = client.search(query, max_pages=max_pages)
        search_logger.info(f"ADAMS returned {len(adams_docs)} documents")

        results = []
        for doc in adams_docs:
            try:
                result = {
                    "title": doc.title,
                    "accession_number": doc.accession_number,
                    "document_type": getattr(doc, "document_type", None),
                    "date": getattr(doc, "document_date", None),
                    "source": "ADAMS",
                    "score": score_relevance(query, doc.title, getattr(doc, "document_type", None)),
                    "rationale": "Matched ADAMS index"
                }
                results.append(result)
            except Exception as e:
                search_logger.warning(f"Error processing ADAMS document: {e}")
                continue

        # Google search if requested
        if use_google:
            if not (GOOGLE_API_KEY and GOOGLE_CX):
                error_msg = "Google search requested but API key/CX not configured"
                search_logger.error(error_msg)
                return {"error": error_msg}

            try:
                rate_limiter.wait()
                search_query = f"site:pbadupws.nrc.gov {query}"
                search_logger.info(f"Searching Google: {search_query}")
                
                google_hits = client.google_search(search_query, num=top_n)
                search_logger.info(f"Google returned {len(google_hits)} results")

                for g in google_hits:
                    try:
                        result = {
                            "title": g.get("title"),
                            "link": g.get("link"),
                            "snippet": g.get("snippet"),
                            "source": "Google",
                            "score": score_relevance(query, g.get("title"), None),
                            "rationale": "Google NRC domain result"
                        }
                        results.append(result)
                    except Exception as e:
                        search_logger.warning(f"Error processing Google result: {e}")
                        continue
                        
            except Exception as e:
                search_logger.error(f"Google search failed: {e}")
                # Continue with ADAMS results only

        # Deduplicate
        seen = set()
        deduped = []
        for r in results:
            try:
                fp = fingerprint_result(r)
                if fp in seen:
                    continue
                seen.add(fp)
                deduped.append(r)
            except Exception as e:
                search_logger.warning(f"Error deduplicating result: {e}")
                continue
        
        search_logger.info(f"After deduplication: {len(deduped)} results")

        # Apply filters
        if filters:
            try:
                deduped = apply_filters(deduped, filters)
            except Exception as e:
                search_logger.error(f"Error applying filters: {e}")
                return {"error": f"Filter error: {str(e)}", "results": deduped}

        # Sort
        try:
            deduped.sort(
                key=lambda r: r.get(sort_by, 0),
                reverse=sort_desc
            )
        except Exception as e:
            search_logger.warning(f"Error sorting results: {e}")

        final_results = deduped[:top_n]
        search_logger.info(f"Returning {len(final_results)} results")
        
        return {
            "results": final_results,
            "total_found": len(results),
            "after_dedup": len(deduped),
            "returned": len(final_results),
            "filters_applied": filters is not None
        }

    except Exception as e:
        search_logger.exception(f"Search failed with unexpected error: {e}")
        return {"error": f"Search failed: {str(e)}", "query": query}

# Tool - Download 
# ------------------------------------------------------------
@mcp.tool()
async def download_adams(accession_number: str) -> Dict[str, Any]:
    """
    Download a document from ADAMS by accession number.
    Args:
        accession_number: The ADAMS accession number (e.g., ML12345A678)
    Returns:
        Dictionary with status, path, and URL or error message
    """
    download_logger.info(f"Download request: {accession_number}")
    
    # Validate accession number
    is_valid, error_msg = validate_accession_number(accession_number)
    if not is_valid:
        download_logger.error(f"Invalid accession number: {error_msg}")
        return {"error": error_msg, "accession_number": accession_number}

    try:
        folder = accession_number[:6]
        url = f"https://pbadupws.nrc.gov/docs/{folder}/{accession_number}.pdf"
        dest = get_downloads_folder() / f"{accession_number}.pdf"
        
        download_logger.info(f"Downloading from: {url}")
        download_logger.info(f"Saving to: {dest}")

        pdf = fetch_pdf(url)
        if not pdf:
            error_msg = "Failed to fetch valid PDF"
            download_logger.error(error_msg)
            return {
                "error": error_msg,
                "url": url,
                "accession_number": accession_number
            }

        dest.write_bytes(pdf)
        download_logger.info(f"Successfully saved PDF: {dest}")
        
        return {
            "status": "success",
            "path": str(dest),
            "url": url,
            "size_bytes": len(pdf),
            "accession_number": accession_number
        }

    except DownloadError as e:
        download_logger.error(f"Download error: {e}")
        return {"error": str(e), "accession_number": accession_number}
    except Exception as e:
        download_logger.exception(f"Unexpected error during download: {e}")
        return {
            "error": f"Download failed: {str(e)}",
            "accession_number": accession_number
        }

# Tool - Batch Download
# ------------------------------------------------------------
@mcp.tool()
async def download_adams_batch(accession_numbers: List[str]) -> Dict[str, Any]:
    """
    Download multiple documents from ADAMS.
    Args:
        accession_numbers: List of ADAMS accession numbers
    Returns:
        Dictionary with folder path and results for each download
    """
    download_logger.info(f"Batch download request: {len(accession_numbers)} documents")
    
    if not accession_numbers:
        return {"error": "No accession numbers provided"}
    
    if len(accession_numbers) > 50:
        return {"error": "Too many documents requested (max 50)"}
    
    folder = get_downloads_folder()
    results = []
    success_count = 0
    failure_count = 0

    for i, acc in enumerate(accession_numbers, 1):
        download_logger.info(f"Processing {i}/{len(accession_numbers)}: {acc}")
        
        # Validate accession number
        is_valid, error_msg = validate_accession_number(acc)
        if not is_valid:
            download_logger.warning(f"Invalid accession number {acc}: {error_msg}")
            results.append({
                "accession": acc,
                "status": "invalid",
                "error": error_msg
            })
            failure_count += 1
            continue

        try:
            url = f"https://pbadupws.nrc.gov/docs/{acc[:6]}/{acc}.pdf"
            dest = folder / f"{acc}.pdf"

            pdf = fetch_pdf(url)
            if pdf:
                dest.write_bytes(pdf)
                results.append({
                    "accession": acc,
                    "status": "success",
                    "path": str(dest),
                    "size_bytes": len(pdf)
                })
                success_count += 1
                download_logger.info(f"Successfully downloaded: {acc}")
            else:
                results.append({
                    "accession": acc,
                    "status": "failed",
                    "error": "Could not fetch PDF"
                })
                failure_count += 1
                download_logger.warning(f"Failed to download: {acc}")
                
        except Exception as e:
            download_logger.error(f"Error downloading {acc}: {e}")
            results.append({
                "accession": acc,
                "status": "error",
                "error": str(e)
            })
            failure_count += 1

    download_logger.info(
        f"Batch download complete: {success_count} success, {failure_count} failed"
    )
    
    return {
        "folder": str(folder),
        "total": len(accession_numbers),
        "success": success_count,
        "failed": failure_count,
        "results": results
    }

# Tool - Summarize PDF
# ------------------------------------------------------------
@mcp.tool()
async def summarize_pdf(path: str, max_chars: int = 2000) -> Dict[str, Any]:
    """
    Extract and summarize text from a PDF file.
    Args:
        path: Path to the PDF file
        max_chars: Maximum characters to extract (default: 2000)
    Returns:
        Dictionary with summary, page count, and character count or error
    """
    pdf_logger.info(f"PDF summary request: {path}")
    
    # Validate path
    try:
        path_obj = Path(path).resolve()
        downloads = get_downloads_folder().resolve()
        
        # Security check: ensure path is within downloads folder
        if not str(path_obj).startswith(str(downloads)):
            error_msg = "Access denied: path outside ADAMS downloads folder"
            pdf_logger.error(f"{error_msg}: {path}")
            return {"error": error_msg, "path": path}
        
        if not path_obj.exists():
            error_msg = "File not found"
            pdf_logger.error(f"{error_msg}: {path}")
            return {"error": error_msg, "path": path}
        
        if not path_obj.is_file():
            error_msg = "Path is not a file"
            pdf_logger.error(f"{error_msg}: {path}")
            return {"error": error_msg, "path": path}
            
    except Exception as e:
        pdf_logger.error(f"Path validation error: {e}")
        return {"error": f"Invalid path: {str(e)}", "path": path}

    try:
        pdf_logger.info(f"Reading PDF: {path_obj}")
        reader = PdfReader(str(path_obj))
        pages = reader.pages
        
        if not pages:
            pdf_logger.warning("PDF has no pages")
            return {
                "error": "PDF contains no pages",
                "path": path,
                "pages": 0
            }
        
        pdf_logger.info(f"PDF has {len(pages)} pages")
        texts = []

        # Extract first page
        try:
            first_page_text = pages[0].extract_text() or ""
            texts.append(first_page_text)
            pdf_logger.info(f"Extracted {len(first_page_text)} chars from first page")
        except Exception as e:
            pdf_logger.warning(f"Could not extract text from first page: {e}")
        
        # Extract last page if different from first
        if len(pages) > 1:
            try:
                last_page_text = pages[-1].extract_text() or ""
                texts.append(last_page_text)
                pdf_logger.info(f"Extracted {len(last_page_text)} chars from last page")
            except Exception as e:
                pdf_logger.warning(f"Could not extract text from last page: {e}")

        # Combine and clean text
        text = " ".join(texts)
        text = " ".join(text.split())  # Normalize whitespace
        
        if not text:
            pdf_logger.warning("No text extracted from PDF")
            return {
                "error": "Could not extract text from PDF (may be image-based)",
                "path": path,
                "pages": len(pages),
                "characters": 0
            }

        summary = chunk_text(text, max_chars)
        
        pdf_logger.info(f"Successfully summarized PDF: {len(summary)} chars")
        
        return {
            "summary": summary,
            "pages": len(pages),
            "characters": len(text),
            "extracted_chars": len(summary),
            "path": path
        }

    except Exception as e:
        pdf_logger.exception(f"PDF processing failed: {e}")
        return {
            "error": f"Failed to process PDF: {str(e)}",
            "path": path
        }

if __name__ == "__main__":
    logger.info("ADAMS MCP Server running")
    mcp.run()


