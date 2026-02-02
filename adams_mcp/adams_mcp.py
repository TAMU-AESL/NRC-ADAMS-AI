"""
ADAMS MCP Server - NRC Document Search and Retrieval (APS / v5)

Uses adams_client_v5 (APS REST API) and auto-enables legacy library only
when the query implies pre-1999 (e.g., 1990-1995).

Tools:
- search_adams
- get_document
- download_adams
- download_adams_batch
- summarize_pdf
"""

import os
import logging
import re
import time
from pathlib import Path
from threading import Lock
from typing import Optional, Dict, List, Any, Tuple
from datetime import datetime, timedelta

from dotenv import load_dotenv
from PyPDF2 import PdfReader
import requests

from mcp.server.fastmcp import FastMCP
from adams_client_v5 import AdamsClient, AdamsDocument, AdamsAPIError

# ------------------------------------------------------------
# Environment
# ------------------------------------------------------------
load_dotenv()

ADAMS_API_KEY = os.getenv("ADAMS_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "3101"))
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "streamable-http")  # stdio, sse, streamable-http

# ------------------------------------------------------------
# Logging
# ------------------------------------------------------------
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)

logger = logging.getLogger("ADAMS_MCP")
search_logger = logging.getLogger("ADAMS_MCP.search")
download_logger = logging.getLogger("ADAMS_MCP.download")
pdf_logger = logging.getLogger("ADAMS_MCP.pdf")

logger.info("Starting ADAMS MCP Server (APS / adams_client_v5)")
logger.info("ADAMS_API_KEY present? %s", bool(os.getenv("ADAMS_API_KEY")))

# ------------------------------------------------------------
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

# ------------------------------------------------------------
# AdamsClient + MCP_Tool
# ------------------------------------------------------------
client = AdamsClient(
    api_key=ADAMS_API_KEY,
    google_api_key=GOOGLE_API_KEY,
    google_cx=GOOGLE_CX,
)

mcp = FastMCP(
    "ADAMS_MCP",
    host=MCP_HOST,
    port=MCP_PORT,
)

if not ADAMS_API_KEY:
    logger.warning(
        "ADAMS_API_KEY not set. API calls will fail. "
        "Get a key from https://adams-api-developer.nrc.gov/"
    )

# ------------------------------------------------------------
# Validation
# ------------------------------------------------------------
def validate_accession_number(accession: str) -> Tuple[bool, Optional[str]]:
    if not accession:
        return False, "Accession number cannot be empty"
    if not isinstance(accession, str):
        return False, "Accession number must be a string"
    accession = accession.strip().upper()
    if not accession.startswith("ML"):
        return False, "Accession number must start with 'ML'"
    if len(accession) < 8:
        return False, "Accession number is too short"
    if not re.match(r"^ML[A-Za-z0-9]+$", accession):
        return False, "Accession number contains invalid characters"
    return True, None


def validate_query(query: str) -> Tuple[bool, Optional[str]]:
    if not query or not query.strip():
        return False, "Query cannot be empty"
    if len(query.strip()) < 2:
        return False, "Query must be at least 2 characters"
    if len(query) > 500:
        return False, "Query is too long (max 500 characters)"
    return True, None

# ------------------------------------------------------------
# Utils: Year Detection
# ------------------------------------------------------------
def extract_year_range(query: str) -> Optional[Tuple[int, int]]:
    """
    Recognizes:
      - "1990-1995"
      - "1990 to 1995"
      - "from 1990 to 1995"
      - single year "1992"
    """
    q = query or ""
    m = re.search(r"(19\d{2}|20\d{2})\s*(?:[-–]|to)\s*(19\d{2}|20\d{2})", q, flags=re.IGNORECASE)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        return (min(y1, y2), max(y1, y2))

    m = re.search(r"(19\d{2}|20\d{2})", q)
    if m:
        y = int(m.group(1))
        return (y, y)

    return None


def query_implies_pre_1999(query: str) -> bool:
    yr = extract_year_range(query)
    if not yr:
        return False
    start_y, _ = yr
    return start_y < 1999


def year_range_to_dates(year_range: Tuple[int, int]) -> Tuple[str, str]:
    start_y, end_y = year_range
    return f"{start_y:04d}-01-01", f"{end_y:04d}-12-31"


def build_api_filters_from_inputs(
    *,
    query: str,
    docket_number: Optional[str],
    document_type: Optional[str],
    days_back: Optional[int],
    user_filters: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Build APS filters using adams_client_v5 helpers where possible.
    Also injects query-implied year range into DocumentDate filters.
    """
    api_filters: List[Dict[str, str]] = []

    # 1) docket/document_type
    if docket_number:
        api_filters.append(client.build_text_filter("DocketNumber", docket_number, "starts"))

    if document_type:
        api_filters.append(client.build_text_filter("DocumentType", document_type, "equals"))

    # 2) days_back => DateAddedTimestamp >= cutoff
    if days_back:
        cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        api_filters.append(client.build_date_filter("DateAddedTimestamp", "ge", cutoff_date))

    # 3) user-provided date_from/date_to => DocumentDate range
    #    (push into API, not post-filter only)
    if user_filters:
        if "date_from" in user_filters:
            api_filters.append(client.build_date_filter("DocumentDate", "ge", str(user_filters["date_from"])))
        if "date_to" in user_filters:
            api_filters.append(client.build_date_filter("DocumentDate", "le", str(user_filters["date_to"])))

        # document_type can also be provided via user_filters
        if "document_type" in user_filters and not document_type:
            dt = user_filters["document_type"]
            if isinstance(dt, str):
                api_filters.append(client.build_text_filter("DocumentType", dt, "equals"))
            elif isinstance(dt, list):
                # AND semantics: multiple equals will be too strict.
                # Use only the first here; users can call multiple searches if needed.
                if dt:
                    api_filters.append(client.build_text_filter("DocumentType", str(dt[0]), "equals"))

    # 4) query-implied year range (only if user didn't already set date_from/date_to)
    yr = extract_year_range(query)
    user_set_dates = bool(user_filters and ("date_from" in user_filters or "date_to" in user_filters))
    if yr and not user_set_dates:
        d_from, d_to = year_range_to_dates(yr)
        api_filters.append(client.build_date_filter("DocumentDate", "ge", d_from))
        api_filters.append(client.build_date_filter("DocumentDate", "le", d_to))

    return api_filters

# ------------------------------------------------------------
# Relevance / dedupe / post-filters
# ------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+", (text or "").lower()))


def score_relevance(query: str, title: Optional[str], doc_type: Optional[str]) -> float:
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


def fingerprint_result(item: dict) -> str:
    if item.get("accession_number"):
        return item["accession_number"]
    if item.get("link"):
        return item["link"].lower().strip()
    return (item.get("title") or "").lower().strip()


def apply_post_filters(results: List[Dict[str, Any]], filters: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not filters:
        return results

    filtered = results

    # min_score (local)
    if "min_score" in filters:
        try:
            min_score = float(filters["min_score"])
            filtered = [r for r in filtered if r.get("score", 0) >= min_score]
        except Exception:
            pass

    # source (back-compat only)
    if "source" in filters:
        src = filters["source"]
        filtered = [r for r in filtered if r.get("source") == src]

    return filtered

# ------------------------------------------------------------
# Downloads Folder
# ------------------------------------------------------------
def get_downloads_folder() -> Path:
    path = Path.home() / "Downloads" / "ADAMS"
    path.mkdir(parents=True, exist_ok=True)
    return path

# ------------------------------------------------------------
# Robust PDF Fetch
# ------------------------------------------------------------
def fetch_pdf(url: str, retries: int = 3, timeout: int = 20) -> Optional[bytes]:
    for attempt in range(1, retries + 1):
        try:
            download_logger.info(f"Fetching PDF (attempt {attempt}/{retries}): {url}")
            response = requests.get(url, timeout=timeout, stream=True)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()
            if "pdf" not in content_type:
                download_logger.warning(f"Non-PDF content type: {content_type}")
                return None

            content_length = response.headers.get("Content-Length")
            if content_length:
                if int(content_length) > 50_000_000:
                    return None

            return response.content

        except requests.exceptions.HTTPError:
            status = getattr(response, "status_code", None)
            if status in (401, 403, 404):
                return None
        except Exception:
            pass

        if attempt < retries:
            time.sleep(2 ** attempt)

    return None

# ------------------------------------------------------------
# TOOL: SEARCH
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
    sort_desc: bool = True,
    docket_number: Optional[str] = None,
    document_type: Optional[str] = None,
    days_back: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Search NRC ADAMS via adams_client_v5 (APS REST API).

    Legacy behavior:
      - legacy_lib is auto-enabled ONLY if query implies a year < 1999
        (e.g., "1990-1995" or "1992").
    """
    search_logger.info(f"Search request: query='{query}', top_n={top_n}, filters={filters}")

    is_valid, error_msg = validate_query(query)
    if not is_valid:
        return {"error": error_msg, "query": query}

    if max_results is not None:
        top_n = max_results

    # auto legacy only when query implies pre-1999
    legacy_lib = query_implies_pre_1999(query)

    # Build API filters (push date constraints into API)
    api_filters = build_api_filters_from_inputs(
        query=query,
        docket_number=docket_number,
        document_type=document_type,
        days_back=days_back,
        user_filters=filters,
    )

    try:
        rate_limiter.wait()

        # Pull extra for dedupe/post-filtering
        api_docs: List[AdamsDocument] = client.search(
            query=query,
            filters=api_filters if api_filters else None,
            max_results=max(top_n * 3, 25),
            max_pages=max_pages,
            legacy_lib=legacy_lib,
        )

        results: List[Dict[str, Any]] = []
        for doc in api_docs:
            results.append({
                "title": doc.title,
                "accession_number": doc.accession_number,
                "document_type": doc.document_type,
                "document_date": doc.document_date,
                "added_date": doc.added_date,
                "docket_number": doc.docket_number,
                "author_name": doc.author_name,
                "url": doc.get_download_url(),
                "source": "ADAMS API",
                "score": score_relevance(query, doc.title, doc.document_type),
                "rationale": "Matched ADAMS API"
            })

        # Optional Google (kept)
        if use_google:
            if not (GOOGLE_API_KEY and GOOGLE_CX):
                return {"error": "Google search requested but API key/CX not configured"}

            rate_limiter.wait()
            # Better targeting for ADAMS PDFs
            search_query = f"site:pbadupws.nrc.gov {query}"
            google_hits = client.google_search(search_query, num=min(top_n, 10))
            for g in google_hits:
                results.append({
                    "title": g.get("title"),
                    "link": g.get("link"),
                    "snippet": g.get("snippet"),
                    "source": "Google",
                    "score": score_relevance(query, g.get("title"), None),
                    "rationale": "Google NRC ADAMS domain result"
                })

        # Deduplicate
        seen = set()
        deduped = []
        for r in results:
            fp = fingerprint_result(r)
            if fp in seen:
                continue
            seen.add(fp)
            deduped.append(r)

        # Post-filters (min_score etc.)
        deduped = apply_post_filters(deduped, filters)

        # Safe sort
        sort_field_map = {
            "score": "score",
            "title": "title",
            "document_date": "document_date",
            "added_date": "added_date",
        }
        sort_field = sort_field_map.get(sort_by, "score")
        deduped.sort(key=lambda r: r.get(sort_field, "") if sort_field != "score" else r.get("score", 0), reverse=sort_desc)

        final_results = deduped[:top_n]
        return {
            "results": final_results,
            "returned": len(final_results),
            "after_dedup": len(deduped),
            "api_filters_applied": len(api_filters),
            "legacy_lib_used": legacy_lib,
        }

    except AdamsAPIError as e:
        search_logger.error(f"ADAMS API error: {e}")
        return {"error": f"ADAMS API error: {str(e)}", "query": query}
    except Exception as e:
        search_logger.exception("Unexpected search failure")
        return {"error": f"Search failed: {str(e)}", "query": query}

# ------------------------------------------------------------
# TOOL: GET DOCUMENT
# ------------------------------------------------------------
@mcp.tool()
async def get_document(accession_number: str) -> Dict[str, Any]:
    """
    Retrieve document metadata from ADAMS by accession number.
    """
    download_logger.info(f"Get document request: {accession_number}")

    is_valid, error_msg = validate_accession_number(accession_number)
    if not is_valid:
        return {"error": error_msg, "accession_number": accession_number}

    try:
        rate_limiter.wait()
        doc = client.get_document(accession_number)

        if not doc:
            return {"error": "Document not found", "accession_number": accession_number}

        return {
            "status": "success",
            "accession_number": doc.accession_number,
            "title": doc.title,
            "document_date": doc.document_date,
            "added_date": doc.added_date,
            "document_type": doc.document_type,
            "author_name": doc.author_name,
            "author_affiliation": doc.author_affiliation,
            "docket_number": doc.docket_number,
            "license_number": doc.license_number,
            "page_count": doc.page_count,
            "url": doc.get_download_url(),
            "keywords": doc.keywords,
            "is_legacy": doc.is_legacy,
            "is_package": doc.is_package,
        }

    except AdamsAPIError as e:
        download_logger.error(f"ADAMS API error: {e}")
        return {"error": f"ADAMS API error: {str(e)}", "accession_number": accession_number}
    except Exception as e:
        download_logger.exception("Unexpected get_document failure")
        return {"error": f"Failed: {str(e)}", "accession_number": accession_number}

# ------------------------------------------------------------
# TOOL: DOWNLOAD SINGLE (API URL first)
# ------------------------------------------------------------
@mcp.tool()
async def download_adams(accession_number: str) -> Dict[str, Any]:
    download_logger.info(f"Download request: {accession_number}")

    is_valid, error_msg = validate_accession_number(accession_number)
    if not is_valid:
        return {"error": error_msg, "accession_number": accession_number}

    accession_number = accession_number.strip().upper()
    dest = get_downloads_folder() / f"{accession_number}.pdf"

    try:
        # 1) Use API to get canonical URL
        rate_limiter.wait()
        doc = client.get_document(accession_number)
        url = doc.get_download_url() if doc else None

        # 2) Fallback patterns (only if needed)
        folder = accession_number[:6]
        urls_to_try = [u for u in [
            url,
            f"https://www.nrc.gov/docs/{folder}/{accession_number}.pdf",
            f"https://pbadupws.nrc.gov/docs/{folder}/{accession_number}.pdf",
        ] if u]

        pdf = None
        used_url = None
        for u in urls_to_try:
            pdf = fetch_pdf(u)
            if pdf:
                used_url = u
                break

        if not pdf:
            return {"error": "Failed to fetch valid PDF", "urls_tried": urls_to_try, "accession_number": accession_number}

        dest.write_bytes(pdf)
        return {"status": "success", "path": str(dest), "url": used_url, "size_bytes": len(pdf), "accession_number": accession_number}

    except AdamsAPIError as e:
        return {"error": f"ADAMS API error: {str(e)}", "accession_number": accession_number}
    except Exception as e:
        download_logger.exception("Unexpected download failure")
        return {"error": f"Download failed: {str(e)}", "accession_number": accession_number}

# ------------------------------------------------------------
# TOOL: BATCH DOWNLOAD (API URL first)
# ------------------------------------------------------------
@mcp.tool()
async def download_adams_batch(accession_numbers: List[str]) -> Dict[str, Any]:
    download_logger.info(f"Batch download request: {len(accession_numbers)} docs")

    if not accession_numbers:
        return {"error": "No accession numbers provided"}
    if len(accession_numbers) > 50:
        return {"error": "Too many documents requested (max 50)"}

    folder_path = get_downloads_folder()
    results = []
    success_count = 0
    failure_count = 0

    for acc in accession_numbers:
        acc = (acc or "").strip().upper()
        is_valid, error_msg = validate_accession_number(acc)
        if not is_valid:
            results.append({"accession": acc, "status": "invalid", "error": error_msg})
            failure_count += 1
            continue

        try:
            rate_limiter.wait()
            doc = client.get_document(acc)
            url = doc.get_download_url() if doc else None

            folder = acc[:6]
            urls_to_try = [u for u in [
                url,
                f"https://www.nrc.gov/docs/{folder}/{acc}.pdf",
                f"https://pbadupws.nrc.gov/docs/{folder}/{acc}.pdf",
            ] if u]

            pdf = None
            used_url = None
            for u in urls_to_try:
                pdf = fetch_pdf(u)
                if pdf:
                    used_url = u
                    break

            if not pdf:
                results.append({"accession": acc, "status": "failed", "error": "Could not fetch PDF", "urls_tried": urls_to_try})
                failure_count += 1
                continue

            dest = folder_path / f"{acc}.pdf"
            dest.write_bytes(pdf)
            results.append({"accession": acc, "status": "success", "path": str(dest), "url": used_url, "size_bytes": len(pdf)})
            success_count += 1

        except Exception as e:
            results.append({"accession": acc, "status": "error", "error": str(e)})
            failure_count += 1

    return {
        "folder": str(folder_path),
        "total": len(accession_numbers),
        "success": success_count,
        "failed": failure_count,
        "results": results
    }

# ------------------------------------------------------------
# TOOL: SUMMARIZE PDF
# ------------------------------------------------------------
def chunk_text(text: str, max_chars: int, chunk_size: int = 1200) -> str:
    chunks = []
    total = 0
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_chars:
            break
    return " ".join(chunks)

@mcp.tool()
async def summarize_pdf(path: str, max_chars: int = 2000) -> Dict[str, Any]:
    pdf_logger.info(f"PDF summary request: {path}")

    try:
        path_obj = Path(path).resolve()
        downloads = get_downloads_folder().resolve()

        if not str(path_obj).startswith(str(downloads)):
            return {"error": "Access denied: path outside ADAMS downloads folder", "path": path}
        if not path_obj.exists():
            return {"error": "File not found", "path": path}
        if not path_obj.is_file():
            return {"error": "Path is not a file", "path": path}
    except Exception as e:
        return {"error": f"Invalid path: {str(e)}", "path": path}

    try:
        reader = PdfReader(str(path_obj))
        pages = reader.pages
        if not pages:
            return {"error": "PDF contains no pages", "path": path, "pages": 0}

        texts = []
        try:
            texts.append(pages[0].extract_text() or "")
        except Exception:
            pass

        if len(pages) > 1:
            try:
                texts.append(pages[-1].extract_text() or "")
            except Exception:
                pass

        text = " ".join(texts)
        text = " ".join(text.split())

        if not text:
            return {"error": "Could not extract text from PDF (may be image-based)", "path": path, "pages": len(pages), "characters": 0}

        summary = chunk_text(text, max_chars)
        return {"summary": summary, "pages": len(pages), "characters": len(text), "extracted_chars": len(summary), "path": path}

    except Exception as e:
        pdf_logger.exception("PDF processing failed")
        return {"error": f"Failed to process PDF: {str(e)}", "path": path}

# ------------------------------------------------------------
# RUN
# ------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting MCP server (FastMCP legacy API)")
    mcp.run(transport="stdio")
