"""
ADAMS MCP Server - NRC Document Search and Retrieval (APS / v5.1)

Tools:
  search_adams        – full-text + filter search with auto legacy detection
  get_document        – metadata by accession number
  download_adams      – download a single PDF
  download_adams_batch – batch download (≤50)
  summarize_pdf       – extract text from a downloaded PDF

Environment:
  ADAMS_API_KEY   – required
  GOOGLE_API_KEY  – optional (enables Google search)
  GOOGLE_CX       – optional (Google Custom Search Engine ID)
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from PyPDF2 import PdfReader

from adams_client_v5 import AdamsAPIError, AdamsClient, AdamsDocument

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()

ADAMS_API_KEY = os.getenv("ADAMS_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("ADAMS_MCP")
logger.info("Starting ADAMS MCP Server")
logger.info("ADAMS_API_KEY present: %s", bool(ADAMS_API_KEY))
logger.info("GOOGLE_API_KEY present: %s", bool(GOOGLE_API_KEY))
logger.info("GOOGLE_CX present: %s", bool(GOOGLE_CX))

client = AdamsClient(
    api_key=ADAMS_API_KEY,
    google_api_key=GOOGLE_API_KEY,
    google_cx=GOOGLE_CX,
)
logger.info("client.google_api_key: %s", bool(client.google_api_key))
logger.info("client.google_cx: %s", bool(client.google_cx))

mcp = FastMCP(
    "ADAMS_MCP",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "3101")),
)

DOWNLOADS_DIR = Path.home() / "Downloads" / "ADAMS"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

if not ADAMS_API_KEY:
    logger.warning(
        "ADAMS_API_KEY not set. API calls will fail. "
        "Get a key from https://adams-api-developer.nrc.gov/"
    )

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, calls_per_minute: int = 20):
        self._interval = 60.0 / calls_per_minute
        self._lock = Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            gap = time.time() - self._last
            if gap < self._interval:
                time.sleep(self._interval - gap)
            self._last = time.time()


_rate = RateLimiter()

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_query(query: str) -> Optional[str]:
    """Return an error string, or None if valid."""
    q = (query or "").strip()
    if not q:
        return "Query cannot be empty"
    if len(q) < 2:
        return "Query must be at least 2 characters"
    if len(q) > 500:
        return "Query too long (max 500 characters)"
    return None


def _validate_accession(acc: str) -> Optional[str]:
    acc = (acc or "").strip().upper()
    if not acc:
        return "Accession number cannot be empty"
    if len(acc) < 6:
        return "Accession number too short"
    if not re.match(r"^[A-Z0-9]+$", acc):
        return "Accession number contains invalid characters"
    return None

# ---------------------------------------------------------------------------
# Year & legacy detection
# ---------------------------------------------------------------------------

def _extract_year_range(query: str) -> Optional[Tuple[int, int]]:
    """
    Detect year or year-range in a query string.
    Recognises:  2010-2015 | 2010–2015 | from 2010 to 2015 | 1990s | 1990
    Returns (start_year, end_year) or None.
    """
    q = query or ""
    # Explicit range  e.g. 1985-1990 or 1985 to 1990
    m = re.search(
        r"\b(1[89]\d{2}|20\d{2})\s*(?:[-–]|to)\s*(1[89]\d{2}|20\d{2})\b",
        q, re.IGNORECASE)
    if m:
        return min(int(m[1]), int(m[2])), max(int(m[1]), int(m[2]))
    # Decade  e.g. 1990s
    m = re.search(r"\b(1[89]\d{2}|20\d{2})s\b", q, re.IGNORECASE)
    if m:
        d = int(m[1])
        return d, d + 9
    # Single year
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", q)
    if m:
        y = int(m[1])
        return y, y
    return None


def _needs_legacy(query: str, year_range: Optional[Tuple[int, int]],
                  date_from: Optional[str] = None) -> bool:
    """
    Return True if the legacy library should be searched.
    Triggers: year range touching pre-2000, explicit date_from before 2000,
    or natural-language era keywords.
    """
    if year_range and year_range[0] < 2000:
        return True
    # Explicit date_from touching pre-2000 also implies legacy content
    if date_from:
        try:
            if int(date_from[:4]) < 2000:
                return True
        except (ValueError, IndexError):
            pass
    return bool(re.search(
        r"\b(legacy|pre-?adams|before\s*(?:19)?99|seventies|eighties|nineties)\b"
        r"|\b19[6-9]\d\b",
        (query or "").lower(),
    ))


def _strip_years(query: str) -> str:
    """Remove year tokens so date filters handle the constraint."""
    q = re.sub(r"\b(1[89]\d{2}|20\d{2})\s*(?:[-–]|to)\s*(1[89]\d{2}|20\d{2})\b",
               " ", query, flags=re.IGNORECASE)
    q = re.sub(r"\b(1[89]\d{2}|20\d{2})s\b", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"\b(1[89]\d{2}|20\d{2})\b", " ", q)
    return re.sub(r"\s+", " ", q).strip()

# ---------------------------------------------------------------------------
# Scoring & dedup 
# ---------------------------------------------------------------------------

def _score(query: str, title: Optional[str], doc_type: Optional[str]) -> float:
    if not title:
        return 0.0
    q_tok = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))
    t_tok = set(re.findall(r"[a-zA-Z0-9]+", title.lower()))
    overlap_ratio = len(q_tok & t_tok) / max(len(q_tok), 1)
    score = overlap_ratio * 10.0
    if query.lower() in title.lower():
        score += 8.0
    dt = (doc_type or "").lower()
    for kw, pts in [("inspection", 2.0), ("reactor", 1.5), ("safety", 1.0)]:
        if kw in dt:
            score += pts
    return round(score, 2)


def _fingerprint(result: Dict) -> str:
    return (result.get("accession_number")
            or (result.get("url") or result.get("link") or "").lower().strip()
            or (result.get("title") or "").lower().strip())


def _dedup(results: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for r in results:
        fp = _fingerprint(r)
        if fp not in seen:
            seen.add(fp)
            out.append(r)
    return out

# ---------------------------------------------------------------------------
# PDF fetch with fallbacks
# ---------------------------------------------------------------------------

def _fetch_pdf(url: str, retries: int = 3, timeout: int = 20) -> Optional[bytes]:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, stream=True)
            resp.raise_for_status()
            if "pdf" not in resp.headers.get("Content-Type", "").lower():
                return None
            size = resp.headers.get("Content-Length")
            if size and int(size) > 50_000_000:
                return None
            return resp.content
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403, 404):
                return None
        except Exception:
            pass
        if attempt < retries:
            time.sleep(2 ** attempt)
    return None


def _pdf_urls(accession: str, api_url: Optional[str]) -> List[str]:
    """Build ordered list of URLs to try when downloading a PDF."""
    acc = accession.strip().upper()
    folder6 = acc[:6]
    urls = []
    if api_url:
        urls.append(api_url)
    urls.append(f"https://www.nrc.gov/docs/{folder6}/{acc}.pdf")
    urls.append(f"https://pbadupws.nrc.gov/docs/{folder6}/{acc}.pdf")
    if not acc.startswith("ML"):
        urls.append(f"https://www.nrc.gov/reading-rm/doc-collections/ACRS/old-reports/{acc}.pdf")
        urls.append(f"https://www.nrc.gov/docs/{acc[:4]}/{acc}.pdf")
    return urls

# ---------------------------------------------------------------------------
# TOOL: ADAMS Search
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_adams(
    query: str,
    top_n: int = 5,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_field: str = "DocumentDate",
    docket_number: Optional[str] = None,
    document_type: Optional[str] = None,
    days_back: Optional[int] = None,
    use_google: bool = True,
    sort_by: str = "score",
    sort_desc: bool = True,
) -> Dict[str, Any]:
    """
    Search NRC ADAMS via the APS REST API.

    Args:
        query:         Full-text search string.
        top_n:         Maximum results to return (default 5).
        date_from:     Start date filter, YYYY-MM-DD.
        date_to:       End date filter, YYYY-MM-DD.
        date_field:    Field to apply date_from/date_to against:
                       "DocumentDate" (default) or "DateAddedTimestamp".
        docket_number: Filter by NRC docket number prefix.
        document_type: Filter by exact document type (e.g. "Inspection Report").
        days_back:     Shorthand: docs added within the last N days
                       (applies to DateAddedTimestamp, independent of date_from/date_to).
        use_google:    Also search Google (site:nrc.gov) and merge results.
                       Silently skipped if GOOGLE_API_KEY/CX are not set.
        sort_by:       "score" | "document_date" | "added_date" | "title".
        sort_desc:     Sort descending (default True).

    Notes:
        - Year references in *query* (e.g. "1992", "1985-1990") automatically
          set DocumentDate filters and enable the legacy library.
        - Explicit date_from / date_to always override query-implied years.
        - Legacy library is also auto-enabled when date_from references a pre-2000 year.
    """
    err = _validate_query(query)
    if err:
        return {"error": err, "query": query}

    logger.info(
        "Search: query='%s' top_n=%d date_from=%s date_to=%s date_field=%s",
        query, top_n, date_from, date_to, date_field,
    )

    # -- Year / legacy detection ------------------------------------------
    yr = _extract_year_range(query)
    use_legacy = _needs_legacy(query, yr, date_from=date_from)
    clean_query = _strip_years(query) if yr else query
    q_api = clean_query.strip() or "a"   # API rejects empty string

    # -- Build API filters -------------------------------------------------
    api_filters = []

    if docket_number:
        api_filters.append(client.text_filter("DocketNumber", docket_number, "starts"))
    if document_type:
        api_filters.append(client.text_filter("DocumentType", document_type, "equals"))
    if days_back:
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        api_filters.append(client.date_filter("DateAddedTimestamp", "ge", cutoff))

    # Explicit date_from / date_to win over inferred year range
    if date_from or date_to:
        if date_from:
            api_filters.append(client.date_filter(date_field, "ge", date_from))
        if date_to:
            api_filters.append(client.date_filter(date_field, "le", date_to))
    elif yr:
        d_from = f"{yr[0]:04d}-01-01"
        d_to   = f"{yr[1]:04d}-12-31"
        api_filters.append(client.date_filter("DocumentDate", "ge", d_from))
        api_filters.append(client.date_filter("DocumentDate", "le", d_to))

    date_range_active = bool(date_from or date_to or yr)
    api_sort = "DocumentDate" if (date_range_active or sort_by == "document_date") else "DateAddedTimestamp"
    api_sort_dir = 1 if sort_desc else 0
    fetch_n = max(top_n * 5, 50)

    # -- Two-pass search (main + legacy) -----------------------------------
    raw_docs: List[AdamsDocument] = []
    try:
        _rate.wait()
        raw_docs += client.search(
            query=q_api, filters=api_filters,
            max_results=fetch_n, main_lib=True, legacy_lib=False,
            sort=api_sort, sort_direction=api_sort_dir,
        )
        if use_legacy:
            _rate.wait()
            # Legacy docs rarely have DocumentDate; drop those filters so results aren't starved
            legacy_filters = [f for f in api_filters
                              if str(f.get("field", "")).lower() != "documentdate"]
            raw_docs += client.search(
                query=q_api, filters=legacy_filters,
                max_results=fetch_n, main_lib=False, legacy_lib=True,
                sort=api_sort, sort_direction=api_sort_dir,
            )
    except AdamsAPIError as e:
        return {"error": f"ADAMS API error: {e}", "query": query}
    except Exception as e:
        logger.exception("Unexpected search error")
        return {"error": f"Search failed: {e}", "query": query}

    # -- Convert to result dicts ------------------------------------------
    results: List[Dict[str, Any]] = [
        {
            "title": doc.title,
            "accession_number": doc.accession_number,
            "document_type": doc.document_type,
            "document_date": doc.document_date,
            "added_date": doc.added_date,
            "docket_number": doc.docket_number,
            "author_name": doc.author_name,
            "url": doc.get_download_url(),
            "source": "ADAMS API",
            "score": _score(query, doc.title, doc.document_type),
        }
        for doc in raw_docs
    ]

    # -- Handle legacy sentinel dates (1900-01-01 = "date unknown") -------
    # For pre-2000 / explicit date-range queries keep them — they may be
    # the only results available in the legacy library.
    if date_range_active:
        def _is_unknown_date(r: Dict[str, Any]) -> bool:
            return (r.get("document_date") or "").strip().startswith("1900-01-01")

        non_unknown = [r for r in results if not _is_unknown_date(r)]
        unknown_docs = [r for r in results if _is_unknown_date(r)]

        if not use_legacy:
            results = non_unknown
        elif non_unknown:
            results = non_unknown + unknown_docs
        else:
            results = unknown_docs

        logger.info(
            "Date-range query: unknown_date=%d non_unknown=%d final=%d",
            len(unknown_docs), len(non_unknown), len(results),
        )

    # -- Google (graceful skip when keys not set) -------------------------
    google_warning = None
    if use_google:
        if client.google_api_key and client.google_cx:
            try:
                _rate.wait()
                seen_urls = {r["url"] for r in results if r.get("url")}
                for g in client.google_search(f"site:nrc.gov {query}", num=top_n):
                    link = g.get("link", "")
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)
                    # Try to extract accession number from URL path
                    acc = None
                    m = re.search(r"/(ML[A-Z0-9]{9,12})\.pdf", link, re.IGNORECASE)
                    if m:
                        acc = m.group(1).upper()
                    results.append({
                        "title": g.get("title"),
                        "accession_number": acc,
                        "url": link,
                        "snippet": g.get("snippet"),
                        "source": "Google",
                        "score": _score(query, g.get("title"), None),
                    })
            except Exception as e:
                google_warning = f"Google search failed: {e}"
                logger.warning(google_warning)
        else:
            google_warning = "Google search skipped: GOOGLE_API_KEY / GOOGLE_CX not configured"
            logger.warning(google_warning)

    # -- Dedup + sort + trim ----------------------------------------------
    results = _dedup(results)

    sort_key_map = {
        "score":         lambda r: r.get("score", 0),
        "document_date": lambda r: r.get("document_date") or "",
        "added_date":    lambda r: r.get("added_date") or "",
        "title":         lambda r: (r.get("title") or "").lower(),
    }
    results.sort(key=sort_key_map.get(sort_by, sort_key_map["score"]), reverse=sort_desc)

    final = results[:top_n]

    out: Dict[str, Any] = {
        "results": final,
        "returned": len(final),
        "total_before_trim": len(results),
        "legacy_lib_used": use_legacy,
        "year_range_detected": yr,
        "date_from_used": date_from,
        "date_to_used": date_to,
        "date_field_used": date_field,
        "date_filters_applied": bool(date_from or date_to or days_back or yr),
        "query_used": clean_query,
    }
    if google_warning:
        out["google_warning"] = google_warning
    return out

# ---------------------------------------------------------------------------
# TOOL: Document Retreival 
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_document(accession_number: str) -> Dict[str, Any]:
    """
    Retrieve full metadata for a single ADAMS document by accession number.

    Args:
        accession_number: NRC accession number (e.g. "ML12345A678").
    """
    err = _validate_accession(accession_number)
    if err:
        return {"error": err, "accession_number": accession_number}

    try:
        _rate.wait()
        doc = client.get_document(accession_number)
    except AdamsAPIError as e:
        return {"error": str(e), "accession_number": accession_number}
    except Exception as e:
        logger.exception("get_document failed")
        return {"error": str(e), "accession_number": accession_number}

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

# ---------------------------------------------------------------------------
# TOOL: ADAMS Download
# ---------------------------------------------------------------------------

@mcp.tool()
async def download_adams(accession_number: str) -> Dict[str, Any]:
    """
    Download a single ADAMS document PDF.

    The file is saved to ~/Downloads/ADAMS/<accession_number>.pdf.

    Args:
        accession_number: NRC accession number (e.g. "ML12345A678").
    """
    err = _validate_accession(accession_number)
    if err:
        return {"error": err, "accession_number": accession_number}

    acc = accession_number.strip().upper()
    dest = DOWNLOADS_DIR / f"{acc}.pdf"

    try:
        _rate.wait()
        doc = client.get_document(acc)
        api_url = doc.get_download_url() if doc else None
    except Exception:
        api_url = None

    pdf = None
    used_url = None
    for url in _pdf_urls(acc, api_url):
        pdf = _fetch_pdf(url)
        if pdf:
            used_url = url
            break

    if not pdf:
        return {"error": "Could not fetch a valid PDF",
                "accession_number": acc,
                "urls_tried": _pdf_urls(acc, api_url)}

    dest.write_bytes(pdf)
    return {"status": "success", "path": str(dest),
            "url": used_url, "size_bytes": len(pdf),
            "accession_number": acc}

# ---------------------------------------------------------------------------
# TOOL: Download Batch
# ---------------------------------------------------------------------------

@mcp.tool()
async def download_adams_batch(accession_numbers: List[str]) -> Dict[str, Any]:
    """
    Download multiple ADAMS document PDFs.

    Files are saved to ~/Downloads/ADAMS/.  Maximum 50 per call.

    Args:
        accession_numbers: List of NRC accession numbers.
    """
    if not accession_numbers:
        return {"error": "No accession numbers provided"}
    if len(accession_numbers) > 50:
        return {"error": "Too many documents (max 50)"}

    results, success, failure = [], 0, 0

    for raw in accession_numbers:
        acc = (raw or "").strip().upper()
        err = _validate_accession(acc)
        if err:
            results.append({"accession": acc, "status": "invalid", "error": err})
            failure += 1
            continue

        try:
            _rate.wait()
            doc = client.get_document(acc)
            api_url = doc.get_download_url() if doc else None
        except Exception:
            api_url = None

        pdf = None
        used_url = None
        for url in _pdf_urls(acc, api_url):
            pdf = _fetch_pdf(url)
            if pdf:
                used_url = url
                break

        if not pdf:
            results.append({"accession": acc, "status": "failed",
                             "error": "Could not fetch PDF"})
            failure += 1
            continue

        dest = DOWNLOADS_DIR / f"{acc}.pdf"
        dest.write_bytes(pdf)
        results.append({"accession": acc, "status": "success",
                        "path": str(dest), "url": used_url, "size_bytes": len(pdf)})
        success += 1

    return {"folder": str(DOWNLOADS_DIR), "total": len(accession_numbers),
            "success": success, "failed": failure, "results": results}

# ---------------------------------------------------------------------------
# TOOL: Summarize PDF
# ---------------------------------------------------------------------------

@mcp.tool()
async def summarize_pdf(path: str, max_chars: int = 2000) -> Dict[str, Any]:
    """
    Extract and return text from a downloaded ADAMS PDF.

    Only files inside ~/Downloads/ADAMS/ are accessible.

    Args:
        path:      Full path to the PDF file.
        max_chars: Maximum characters to return (default 2000).
    """
    try:
        p = Path(path).resolve()
        if not str(p).startswith(str(DOWNLOADS_DIR.resolve())):
            return {"error": "Access denied: path outside ADAMS downloads folder", "path": path}
        if not p.is_file():
            return {"error": "File not found", "path": path}
    except Exception as e:
        return {"error": f"Invalid path: {e}", "path": path}

    try:
        reader = PdfReader(str(p))
        pages = reader.pages
        if not pages:
            return {"error": "PDF has no pages", "path": path}

        raw = " ".join(
            filter(None, [
                (pages[0].extract_text() or ""),
                (pages[-1].extract_text() or "") if len(pages) > 1 else "",
            ])
        )
        text = " ".join(raw.split())
        if not text:
            return {"error": "Could not extract text (may be image-based PDF)",
                    "path": path, "pages": len(pages)}

        excerpt = text[:max_chars]
        return {"summary": excerpt, "pages": len(pages),
                "characters": len(text), "extracted_chars": len(excerpt), "path": path}
    except Exception as e:
        logger.exception("summarize_pdf failed")
        return {"error": f"PDF processing failed: {e}", "path": path}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Running MCP server (stdio transport)")
    mcp.run(transport="stdio")