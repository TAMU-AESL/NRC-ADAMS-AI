"""
ADAMS Client v5 - NRC ADAMS Public Search API (APS REST)

As of right now, utilize Claude Desktop with the MCP setup to run this code.
To utilize your API key, set the ADAMS_API_KEY environment variable in the MCP config. This can be added under your PYTHONPATH variable.
Google Search should be able to be utilized via your own Google API & Google Search Engine Keys also in the config files, if you so wish.

API Docs:  https://adams-api-developer.nrc.gov/
Endpoints:
  POST /aps/api/search              – search
  GET  /aps/api/search/{accession}  – single document

Auth: Ocp-Apim-Subscription-Key header (set ADAMS_API_KEY env var)

Author: TAMU-AESL  |  Version: 5.1.0
"""

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("ADAMS_CLIENT_V5")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AdamsAPIError(Exception):
    """Raised on ADAMS API request or processing errors."""

# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------

class AdamsDocument:
    """A single document record returned by ADAMS."""

    # Fields that the API may return as lists
    _LIST_FIELDS = (
        "document_type", "author_name", "author_affiliation",
        "addressee_name", "addressee_affiliation",
        "docket_number", "license_number", "document_report_number", "keywords",
    )

    def __init__(
        self,
        title: str = None,
        accession_number: str = None,
        document_date: str = None,
        added_date: str = None,
        document_type: Union[str, List[str]] = None,
        author_name: Union[str, List[str]] = None,
        author_affiliation: Union[str, List[str]] = None,
        addressee_name: Union[str, List[str]] = None,
        addressee_affiliation: Union[str, List[str]] = None,
        docket_number: Union[str, List[str]] = None,
        license_number: Union[str, List[str]] = None,
        package_number: str = None,
        document_report_number: Union[str, List[str]] = None,
        keywords: Union[str, List[str]] = None,
        page_count: int = None,
        uri: str = None,
        content: str = None,
        is_package: bool = False,
        is_legacy: bool = False,
        availability: str = None,
    ):
        self.title = title
        self.accession_number = accession_number
        self.document_date = document_date
        self.added_date = added_date
        self.package_number = package_number
        self.uri = uri
        self.content = content
        self.is_package = is_package
        self.is_legacy = is_legacy
        self.availability = availability

        # Normalize list-or-string fields → joined string + raw list
        self.document_type = self._join(document_type)
        self.document_types: List[str] = self._to_list(document_type)
        self.author_name = self._join(author_name)
        self.author_affiliation = self._join(author_affiliation)
        self.addressee_name = self._join(addressee_name)
        self.addressee_affiliation = self._join(addressee_affiliation)
        self.docket_number = self._join(docket_number)
        self.docket_numbers: List[str] = self._to_list(docket_number)
        self.license_number = self._join(license_number)
        self.document_report_number = self._join(document_report_number)
        self.keywords = self._join(keywords)

        try:
            self.page_count = int(page_count) if page_count not in (None, "", "None") else None
        except (ValueError, TypeError):
            self.page_count = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _join(value: Union[str, List, None], sep: str = ", ") -> Optional[str]:
        if value is None:
            return None
        return sep.join(str(v) for v in value if v) if isinstance(value, list) else str(value)

    @staticmethod
    def _to_list(value: Union[str, List, None]) -> List[str]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "AdamsDocument":
        """Build from the APS API document payload."""
        return cls(
            title=data.get("DocumentTitle") or data.get("Name"),
            accession_number=data.get("AccessionNumber"),
            document_date=data.get("DocumentDate"),
            added_date=data.get("DateAddedTimestamp") or data.get("DateAdded"),
            document_type=data.get("DocumentType"),
            author_name=data.get("AuthorName"),
            author_affiliation=data.get("AuthorAffiliation"),
            addressee_name=data.get("AddresseeName"),
            addressee_affiliation=data.get("AddresseeAffiliation"),
            docket_number=data.get("DocketNumber"),
            license_number=data.get("LicenseNumber"),
            package_number=data.get("PackageNumber"),
            document_report_number=data.get("DocumentReportNumber"),
            keywords=data.get("Keyword"),
            page_count=data.get("EstimatedPageCount"),
            uri=data.get("Url"),
            content=data.get("content"),
            is_package=data.get("IsPackage") == "Yes",
            is_legacy=data.get("IsLegacy") == "Yes",
            availability=data.get("Availability"),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "accession_number": self.accession_number,
            "document_date": self.document_date,
            "added_date": self.added_date,
            "document_type": self.document_type,
            "document_types": self.document_types,
            "author_name": self.author_name,
            "author_affiliation": self.author_affiliation,
            "addressee_name": self.addressee_name,
            "addressee_affiliation": self.addressee_affiliation,
            "docket_number": self.docket_number,
            "docket_numbers": self.docket_numbers,
            "license_number": self.license_number,
            "package_number": self.package_number,
            "document_report_number": self.document_report_number,
            "keywords": self.keywords,
            "page_count": self.page_count,
            "uri": self.uri,
            "is_package": self.is_package,
            "is_legacy": self.is_legacy,
            "availability": self.availability,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    def get_download_url(self) -> Optional[str]:
        if self.uri:
            return self.uri
        if self.accession_number and self.accession_number.startswith("ML"):
            acc = self.accession_number
            return f"https://www.nrc.gov/docs/{acc[:6]}/{acc}.pdf"
        return None

    def download(self, directory: str = ".", filename: str = None, skip_existing: bool = True) -> str:
        """Download the PDF to *directory*. Returns the local file path."""
        if not self.accession_number:
            raise AdamsAPIError("No accession number; cannot download.")
        url = self.get_download_url()
        if not url:
            raise AdamsAPIError(f"Cannot build download URL for {self.accession_number}")

        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename or f"{self.accession_number}.pdf")

        if skip_existing and os.path.exists(path):
            return path

        try:
            resp = requests.get(url, stream=True, timeout=30)
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)
        except requests.RequestException as e:
            raise AdamsAPIError(f"Download failed: {e}")

        return path

    def __repr__(self):
        return f"<AdamsDocument {self.accession_number or '(no accession)'}>"

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AdamsClient:
    """
    ADAMS REST API client.

    Environment variables:
      ADAMS_API_KEY   – required for API calls
      GOOGLE_API_KEY  – optional, enables google_search()
      GOOGLE_CX       – optional, Google Custom Search Engine ID
    """

    BASE_URL = "https://adams-api.nrc.gov/aps/api/search"
    GOOGLE_URL = "https://www.googleapis.com/customsearch/v1"
    PAGE_SIZE = 100   # API max per page
    TIMEOUT = 60

    def __init__(
        self,
        api_key: str = None,
        google_api_key: str = None,
        google_cx: str = None,
        base_url: str = None,
        debug: bool = False,
        timeout: int = None,
    ):
        self.api_key = api_key or os.environ.get("ADAMS_API_KEY")
        self.google_api_key = google_api_key or os.environ.get("GOOGLE_API_KEY")
        self.google_cx = google_cx or os.environ.get("GOOGLE_CX")
        self.base_url = base_url or self.BASE_URL
        self.timeout = timeout or self.TIMEOUT

        if debug:
            logger.setLevel(logging.DEBUG)

        self.session = requests.Session()
        retry = Retry(total=3, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET", "POST"])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = 300  # seconds
        self.last_search: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Filter builders (static, re-usable by MCP layer too)
    # ------------------------------------------------------------------

    @staticmethod
    def text_filter(field: str, value: str, operator: str = "contains") -> Dict[str, str]:
        """
        Build a text filter.  operator: contains | notcontains | starts |
        notstarts | equals | notequals
        """
        return {"field": field, "value": value, "operator": operator}

    @staticmethod
    def date_filter(field: str, op: str, date: str) -> Dict[str, str]:
        """
        Build a single-bound date filter.
        op: "ge" (≥), "le" (≤), "eq" (=)
        date: YYYY-MM-DD
        """
        return {"field": field, "value": f"({field} {op} '{date}')"}

    @staticmethod
    def date_range_filter(field: str, start: str, end: str) -> Dict[str, str]:
        """Build a date-range filter (ge start AND le end)."""
        return {"field": field,
                "value": f"({field} ge '{start}') and ({field} le '{end}')"}

    # Aliases kept for back-compat
    build_text_filter = text_filter
    build_date_filter = date_filter
    build_date_range_filter = date_range_filter

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise AdamsAPIError(
                "ADAMS API key not set. Pass api_key= or set ADAMS_API_KEY.")
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": self.api_key,
        }

    def _cache_key(self, **kwargs) -> str:
        raw = json.dumps(kwargs, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _from_cache(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        value, exp = entry
        if time.time() > exp:
            del self._cache[key]
            return None
        return value

    def _to_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (value, time.time() + self._cache_ttl)

    # ------------------------------------------------------------------
    # Core search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str = "",
        filters: List[Dict] = None,
        any_filters: List[Dict] = None,
        main_lib: bool = True,
        legacy_lib: bool = False,
        sort: str = "DateAddedTimestamp",
        sort_direction: int = 1,   # 1 = desc, 0 = asc
        max_results: int = 100,
        max_pages: int = 10,
        use_cache: bool = True,
    ) -> List[AdamsDocument]:
        """
        Search ADAMS.  Returns up to *max_results* AdamsDocument objects.
        Paginates automatically up to *max_pages*.
        """
        filters = filters or []
        any_filters = any_filters or []

        cache_key = self._cache_key(
            query=query, filters=filters, any_filters=any_filters,
            main_lib=main_lib, legacy_lib=legacy_lib,
            sort=sort, sort_direction=sort_direction, max_results=max_results,
        )
        if use_cache:
            cached = self._from_cache(cache_key)
            if cached is not None:
                logger.debug("[search] cache hit")
                return cached

        docs: List[AdamsDocument] = []
        skip = 0

        for page in range(max_pages):
            payload = {
                "q": query,
                "filters": filters,
                "anyFilters": any_filters,
                "mainLibFilter": main_lib,
                "legacyLibFilter": legacy_lib,
                "sort": sort,
                "sortDirection": sort_direction,
                "skip": skip,
            }
            logger.debug("[search] page %d payload: %s", page + 1, payload)

            try:
                resp = self.session.post(
                    self.base_url, headers=self._headers,
                    json=payload, timeout=self.timeout)
                resp.raise_for_status()
            except requests.exceptions.Timeout:
                raise AdamsAPIError(f"Request timed out after {self.timeout}s")
            except requests.exceptions.HTTPError as e:
                detail = ""
                try:
                    detail = resp.json()
                except Exception:
                    pass
                raise AdamsAPIError(f"HTTP {resp.status_code}: {e} {detail}")
            except requests.RequestException as e:
                raise AdamsAPIError(f"Request failed: {e}")

            results = resp.json().get("results", [])
            if not results:
                break

            for r in results:
                docs.append(AdamsDocument.from_api_response(r.get("document", {})))
                if len(docs) >= max_results:
                    break

            if len(docs) >= max_results or len(results) < self.PAGE_SIZE:
                break

            skip += self.PAGE_SIZE

        docs = docs[:max_results]
        self.last_search = {
            "query": query, "filters": filters,
            "main_lib": main_lib, "legacy_lib": legacy_lib,
            "result_count": len(docs),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        if use_cache:
            self._to_cache(cache_key, docs)

        logger.info("[search] %d documents returned", len(docs))
        return docs

    # ------------------------------------------------------------------
    # Single document retrieval
    # ------------------------------------------------------------------

    def get_document(self, accession_number: str) -> Optional[AdamsDocument]:
        """Retrieve a single document by accession number. Returns None if 404."""
        if not accession_number:
            raise AdamsAPIError("accession_number is required")
        acc = accession_number.strip().upper()
        try:
            resp = self.session.get(
                f"{self.base_url}/{acc}", headers=self._headers, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError:
            if resp.status_code == 404:
                return None
            raise AdamsAPIError(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            raise AdamsAPIError(f"Request failed: {e}")

        data = resp.json()
        return AdamsDocument.from_api_response(data.get("document", data))

    # ------------------------------------------------------------------
    # Convenience search wrappers
    # ------------------------------------------------------------------

    def search_by_docket(self, docket_number: str, max_results: int = 100,
                         days_back: int = 365, **kwargs) -> List[AdamsDocument]:
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        return self.search(filters=[
            self.text_filter("DocketNumber", docket_number, "starts"),
            self.date_filter("DateAddedTimestamp", "ge", cutoff),
        ], max_results=max_results, **kwargs)

    def search_by_document_type(self, document_type: str, query: str = "",
                                max_results: int = 100, **kwargs) -> List[AdamsDocument]:
        return self.search(query=query,
                           filters=[self.text_filter("DocumentType", document_type, "equals")],
                           max_results=max_results, **kwargs)

    def search_recent(self, days: int = 30, query: str = "",
                      max_results: int = 100, **kwargs) -> List[AdamsDocument]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self.search(query=query,
                           filters=[self.date_filter("DateAddedTimestamp", "ge", cutoff)],
                           max_results=max_results, **kwargs)

    # ------------------------------------------------------------------
    # Google Custom Search
    # ------------------------------------------------------------------

    def google_search(self, query: str, num: int = 10) -> List[Dict[str, Any]]:
        """
        Search Google Custom Search (site:nrc.gov).
        Requires GOOGLE_API_KEY and GOOGLE_CX.
        """
        if not self.google_api_key or not self.google_cx:
            raise AdamsAPIError(
                "Google search requires GOOGLE_API_KEY and GOOGLE_CX env vars.")
        try:
            resp = self.session.get(
                self.GOOGLE_URL,
                params={"key": self.google_api_key, "cx": self.google_cx,
                        "q": query, "num": min(num, 10)},
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AdamsAPIError(f"Google search failed: {e}")

        return [
            {"title": item.get("title"), "link": item.get("link"),
             "snippet": item.get("snippet"), "source": "Google"}
            for item in resp.json().get("items", [])
            if isinstance(item.get("link"), str)
            and "@" not in item["link"]
            and not item["link"].startswith("mailto:")
        ]

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def save_results_to_json(self, documents: List[AdamsDocument],
                             filepath: str, include_metadata: bool = True) -> str:
        output: Dict[str, Any] = {
            "documents": [d.to_dict() for d in documents],
            "count": len(documents),
        }
        if include_metadata and self.last_search:
            output["search_metadata"] = self.last_search
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2)
        return filepath

    def download_documents(self, documents: List[AdamsDocument], directory: str = ".",
                           skip_existing: bool = True,
                           max_concurrent: int = 4) -> List[Dict[str, Any]]:
        """Download multiple documents concurrently."""
        os.makedirs(directory, exist_ok=True)

        def _one(doc):
            try:
                return {"accession_number": doc.accession_number, "status": "success",
                        "path": doc.download(directory=directory, skip_existing=skip_existing)}
            except Exception as e:
                return {"accession_number": doc.accession_number, "status": "error", "error": str(e)}

        with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
            futures = {ex.submit(_one, doc): doc for doc in documents}
            return [f.result() for f in as_completed(futures)]

# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------

def create_client(
    api_key: str = None,
    google_api_key: str = None,
    google_cx: str = None,
    debug: bool = False,
) -> AdamsClient:
    """Create an AdamsClient from parameters or environment variables."""
    return AdamsClient(
        api_key=api_key,
        google_api_key=google_api_key,
        google_cx=google_cx,
        debug=debug,
    )
