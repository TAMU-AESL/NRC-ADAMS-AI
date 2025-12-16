import os
import logging
import re
import time
from pathlib import Path
from threading import Lock
from typing import Optional

from dotenv import load_dotenv
from PyPDF2 import PdfReader
import requests

from mcp.server.fastmcp import FastMCP
from adams_client_v4 import AdamsClient

# Environment
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")

# Logging
# ------------------------------------------------------------
logging.basicConfig(
    filename="mcp_server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logging.info("Starting ADAMS MCP Server")

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

# Util — Tokenization & Relevance
# ------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9]+", text.lower()))

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

# Util - Deduplication
# ------------------------------------------------------------
def fingerprint_result(item: dict) -> str:
    if item.get("accession_number"):
        return item["accession_number"]
    if item.get("link"):
        return item["link"].lower().strip()
    return (item.get("title") or "").lower().strip()

# Util — Robust PDF Fetch
# ------------------------------------------------------------
def fetch_pdf(url: str, retries: int = 3, timeout: int = 20) -> Optional[bytes]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=timeout)
            if (
                r.status_code == 200
                and "pdf" in r.headers.get("Content-Type", "").lower()
            ):
                return r.content
        except Exception:
            pass

        time.sleep(1.0 * attempt)

    return None

# Util - Chunked Text Collection
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

# Downloads Folder
# ------------------------------------------------------------
def get_downloads_folder() -> Path:
    path = Path.home() / "Downloads" / "ADAMS"
    path.mkdir(parents=True, exist_ok=True)
    return path

# Tool - Search_ADAMS
# ------------------------------------------------------------
@mcp.tool()
async def search_adams(
    query: str,
    top_n: int = 5,
    max_results: Optional[int] = None,
    max_pages: int = 1,
    use_google: bool = False,
    filters: dict | None = None,
    sort_by: str = "score",
    sort_desc: bool = True
):
    """
    Search NRC ADAMS (optionally Google).
    """

    if not query.strip():
        return {"error": "Query cannot be empty"}

    if max_results is not None:
        top_n = max_results

    try:
        rate_limiter.wait()
        adams_docs = client.search(query, max_pages=max_pages)

        results = []
        for doc in adams_docs:
            results.append({
                "title": doc.title,
                "accession_number": doc.accession_number,
                "document_type": getattr(doc, "document_type", None),
                "source": "ADAMS",
                "score": score_relevance(query, doc.title, getattr(doc, "document_type", None)),
                "rationale": "Matched ADAMS index"
            })

        if use_google:
            if not (GOOGLE_API_KEY and GOOGLE_CX):
                return {"error": "Google search requested but API key/CX not configured"}

            rate_limiter.wait()
            google_hits = client.google_search(
                f"site:pbadupws.nrc.gov {query}",
                num=top_n
            )

            for g in google_hits:
                results.append({
                    "title": g.get("title"),
                    "link": g.get("link"),
                    "snippet": g.get("snippet"),
                    "source": "Google",
                    "score": score_relevance(query, g.get("title"), None),
                    "rationale": "Google NRC domain result"
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

        # Sort
        deduped.sort(
            key=lambda r: r.get(sort_by, 0),
            reverse=sort_desc
        )

        return {"results": deduped[:top_n]}

    except Exception as e:
        logging.exception("search_adams failed")
        return {"error": str(e)}

# Tool - Download 
# ------------------------------------------------------------
@mcp.tool()
async def download_adams(accession_number: str):
    if not accession_number.startswith("ML"):
        return {"error": "Invalid accession number"}

    folder = accession_number[:6]
    url = f"https://pbadupws.nrc.gov/docs/{folder}/{accession_number}.pdf"
    dest = get_downloads_folder() / f"{accession_number}.pdf"

    try:
        pdf = fetch_pdf(url)
        if not pdf:
            return {"error": "Failed to fetch valid PDF", "url": url}

        dest.write_bytes(pdf)
        return {"status": "success", "path": str(dest), "url": url}

    except Exception as e:
        logging.exception("download_adams failed")
        return {"error": str(e)}

# Tool - Batch Download
# ------------------------------------------------------------
@mcp.tool()
async def download_adams_batch(accession_numbers: list[str]):
    folder = get_downloads_folder()
    results = []

    for acc in accession_numbers:
        if not acc.startswith("ML"):
            results.append({"accession": acc, "status": "invalid"})
            continue

        url = f"https://pbadupws.nrc.gov/docs/{acc[:6]}/{acc}.pdf"
        dest = folder / f"{acc}.pdf"

        try:
            pdf = fetch_pdf(url)
            if pdf:
                dest.write_bytes(pdf)
                results.append({"accession": acc, "status": "success"})
            else:
                results.append({"accession": acc, "status": "failed"})
        except Exception as e:
            results.append({"accession": acc, "status": str(e)})

    return {"folder": str(folder), "results": results}

# Tool - Summarize PDF
# ------------------------------------------------------------
@mcp.tool()
async def summarize_pdf(path: str, max_chars: int = 2000):
    if not os.path.exists(path):
        return {"error": "File not found"}

    try:
        reader = PdfReader(path)
        pages = reader.pages
        texts = []

        if pages:
            texts.append(pages[0].extract_text() or "")
            if len(pages) > 1:
                texts.append(pages[-1].extract_text() or "")

        text = " ".join(texts)
        text = " ".join(text.split())

        summary = chunk_text(text, max_chars)

        return {
            "summary": summary,
            "pages": len(pages),
            "characters": len(text)
        }

    except Exception as e:
        logging.exception("summarize_pdf failed")
        return {"error": str(e)}

if __name__ == "__main__":
    logging.info("ADAMS MCP Server running")
    mcp.run()

