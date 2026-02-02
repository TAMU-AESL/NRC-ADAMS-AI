"""
ADAMS Client v5 - New REST API Integration

This client uses the new NRC ADAMS Public Search API (https://adams-api.nrc.gov)
which replaced the legacy XML-based API.

API Documentation: https://adams-api-developer.nrc.gov/
Developer Guide: APS-API-Guide.pdf
As of right now, utilize Claude Desktop with the MCP setup to run this code.
To utilize your API key, set the ADAMS_API_KEY environment variable in the MCP config. This can be added under your PYTHONPATH variable.

Key Changes from v4:
- JSON-based REST API instead of XML
- Subscription key authentication (Ocp-Apim-Subscription-Key header)
- POST /aps/api/search for searching
- GET /aps/api/search/{accessionNumber} for single document retrieval
- New filter format with field, value, operator structure
- Date filters use OData-style expressions

Author: TAMU-AESL
Version: 5.0.0
"""

import os
import json
import logging
import time
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────
# Logging Setup
# ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ADAMS_CLIENT_V5")


class AdamsAPIError(Exception):
    """Custom exception for errors related to ADAMS API requests or processing."""
    pass


class AdamsDocument:
    """
    Represents a document record returned by an ADAMS search.

    Updated for the new API response format which uses different field names
    and returns arrays for multi-value fields.
    """

    def __init__(
        self,
        title: str = None,
        accession_number: str = None,
        document_date: str = None,
        added_date: str = None,  # DateAddedTimestamp in new API
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
        content_size: int = None,
        mime_type: str = None,
        uri: str = None,
        content: str = None,  # New: document content text
        is_package: bool = False,
        is_legacy: bool = False,
        availability: str = None,
    ):
        self.title = title
        self.accession_number = accession_number
        self.document_date = document_date
        self.added_date = added_date

        # Handle list/string fields - normalize to string for compatibility
        self.document_type = self._join_list(document_type)
        self.document_types = document_type if isinstance(document_type, list) else [document_type] if document_type else []
        self.author_name = self._join_list(author_name)
        self.author_affiliation = self._join_list(author_affiliation)
        self.addressee_name = self._join_list(addressee_name)
        self.addressee_affiliation = self._join_list(addressee_affiliation)
        self.docket_number = self._join_list(docket_number)
        self.docket_numbers = docket_number if isinstance(docket_number, list) else [docket_number] if docket_number else []
        self.license_number = self._join_list(license_number)
        self.package_number = package_number
        self.document_report_number = self._join_list(document_report_number)
        self.keywords = self._join_list(keywords)

        # Parse numeric fields
        try:
            self.page_count = int(page_count) if page_count not in (None, "", "None") else None
        except (ValueError, TypeError):
            self.page_count = None

        if content_size is not None and isinstance(content_size, str):
            try:
                self.content_size = int(content_size.replace(",", ""))
            except ValueError:
                self.content_size = None
        else:
            self.content_size = content_size if content_size not in (None, "", "None") else None

        self.mime_type = mime_type
        self.uri = uri
        self.content = content
        self.is_package = is_package
        self.is_legacy = is_legacy
        self.availability = availability

    @staticmethod
    def _join_list(value: Union[str, List[str], None], separator: str = ", ") -> Optional[str]:
        """Join a list into a string, or return the string as-is."""
        if value is None:
            return None
        if isinstance(value, list):
            return separator.join(str(v) for v in value if v)
        return str(value)

    @classmethod
    def from_api_response(cls, doc_data: Dict[str, Any]) -> "AdamsDocument":
        """
        Create an AdamsDocument from the new API's document response format.

        The new API returns documents with these field names (case-sensitive):
        - AccessionNumber, DocumentTitle, DocumentDate, DateAddedTimestamp
        - DocumentType (array), AuthorName (array), AuthorAffiliation (array)
        - AddresseeName (array), AddresseeAffiliation (array)
        - DocketNumber (array), LicenseNumber (array), Keyword (array)
        - EstimatedPageCount, Url, content, IsPackage, IsLegacy
        """
        return cls(
            title=doc_data.get("DocumentTitle") or doc_data.get("Name"),
            accession_number=doc_data.get("AccessionNumber"),
            document_date=doc_data.get("DocumentDate"),
            added_date=doc_data.get("DateAddedTimestamp") or doc_data.get("DateAdded"),
            document_type=doc_data.get("DocumentType"),
            author_name=doc_data.get("AuthorName"),
            author_affiliation=doc_data.get("AuthorAffiliation"),
            addressee_name=doc_data.get("AddresseeName"),
            addressee_affiliation=doc_data.get("AddresseeAffiliation"),
            docket_number=doc_data.get("DocketNumber"),
            license_number=doc_data.get("LicenseNumber"),
            package_number=doc_data.get("PackageNumber"),
            document_report_number=doc_data.get("DocumentReportNumber"),
            keywords=doc_data.get("Keyword"),
            page_count=doc_data.get("EstimatedPageCount"),
            uri=doc_data.get("Url"),
            content=doc_data.get("content"),
            is_package=doc_data.get("IsPackage") == "Yes",
            is_legacy=doc_data.get("IsLegacy") == "Yes",
            availability=doc_data.get("Availability"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return the document's metadata as a dictionary."""
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
            "content_size": self.content_size,
            "mime_type": self.mime_type,
            "uri": self.uri,
            "is_package": self.is_package,
            "is_legacy": self.is_legacy,
            "availability": self.availability,
        }

    def to_json(self) -> str:
        """Return the document as a JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def get_download_url(self) -> Optional[str]:
        """Get the download URL for this document."""
        if self.uri:
            return self.uri
        if self.accession_number and self.accession_number.startswith("ML"):
            acc = self.accession_number
            folder = acc[:6]
            return f"https://www.nrc.gov/docs/{folder}/{acc}.pdf"
        return None

    def download(self, directory: str = ".", filename: str = None, skip_existing: bool = True) -> str:
        """Download the document's file (PDF) to the specified directory."""
        if not self.accession_number:
            raise AdamsAPIError("No accession number available; cannot download.")

        download_url = self.get_download_url()
        if not download_url:
            raise AdamsAPIError(f"Cannot determine download URL for accession: {self.accession_number}")

        os.makedirs(directory, exist_ok=True)
        filename = filename or f"{self.accession_number}.pdf"
        file_path = os.path.join(directory, filename)

        if skip_existing and os.path.exists(file_path):
            logger.debug("Skipping existing file: %s", file_path)
            return file_path

        try:
            response = requests.get(download_url, stream=True, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise AdamsAPIError(f"Download request failed: {e}")

        try:
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        except Exception as e:
            raise AdamsAPIError(f"Error saving file {file_path}: {e}")

        return file_path

    def __repr__(self):
        return f"<AdamsDocument {self.accession_number or '(no accession)'}>"

class AdamsClient:
    """
    ADAMS Client v5 - New REST API

    Uses the new NRC ADAMS Public Search API at https://adams-api.nrc.gov

    Features:
    - JSON-based REST API
    - Subscription key authentication
    - Boolean search with filters
    - Support for main and legacy libraries
    - Pagination support
    - Optional Google Custom Search integration

    API Endpoints:
    - POST /aps/api/search - Search document library
    - GET /aps/api/search/{accessionNumber} - Get single document
    """

    # API Configuration
    DEFAULT_BASE_URL = "https://adams-api.nrc.gov/aps/api/search"
    DEFAULT_TIMEOUT = 60
    DEFAULT_PAGE_SIZE = 100  # API maximum per request

    # Filter operators (from API guide)
    OPERATORS = {
        "contains": "contains",
        "not_contains": "notcontains",
        "starts": "starts",
        "not_starts": "notstarts",
        "equals": "equals",
        "not_equals": "notequals",
    }

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        debug: bool = False,
        google_api_key: str = None,
        google_cx: str = None,
        timeout: int = None,
    ):
        """
        Initialize the ADAMS Client.

        Args:
            api_key: ADAMS API subscription key (required for API calls).
                     Can also be set via ADAMS_API_KEY environment variable.
            base_url: Override the default API base URL.
            debug: Enable debug logging.
            google_api_key: Google Custom Search API key (optional).
            google_cx: Google Custom Search Engine ID (optional).
            timeout: Request timeout in seconds.
        """
        self.api_key = api_key or os.environ.get("ADAMS_API_KEY")
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.debug = debug
        self.timeout = timeout or self.DEFAULT_TIMEOUT

        # Google API configuration
        self.google_api_key = google_api_key or os.environ.get("GOOGLE_API_KEY")
        self.google_cx = google_cx or os.environ.get("GOOGLE_CX")
        self.google_base_url = "https://www.googleapis.com/customsearch/v1"

        # Session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))

        # Cache for search results
        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = 300  # 5 minutes

        # Last search metadata
        self._last_search: Dict[str, Any] = {}

        if self.debug:
            logger.setLevel(logging.DEBUG)

    def _get_headers(self) -> Dict[str, str]:
        """Get the required headers for API requests."""
        if not self.api_key:
            raise AdamsAPIError(
                "ADAMS API key not configured. "
                "Set api_key parameter or ADAMS_API_KEY environment variable."
            )
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Ocp-Apim-Subscription-Key": self.api_key,
        }

    # ─────────────────────────────────────────────
    # Cache Helpers
    # ─────────────────────────────────────────────

    def _make_cache_key(self, prefix: str, **kwargs) -> str:
        """Create a stable hashed key from kwargs."""
        raw = prefix + json.dumps(kwargs, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cache(self, key: str) -> Optional[Any]:
        """Get a cached value if not expired."""
        entry = self._cache.get(key)
        if not entry:
            return None
        value, expiry = entry
        if time.time() > expiry:
            del self._cache[key]
            return None
        return value

    def _set_cache(self, key: str, value: Any) -> None:
        """Set a cache entry with TTL."""
        expiry = time.time() + self._cache_ttl
        self._cache[key] = (value, expiry)

    # ─────────────────────────────────────────────
    # Filtering
    # ─────────────────────────────────────────────

    @staticmethod
    def build_text_filter(field: str, value: str, operator: str = "contains") -> Dict[str, str]:
        """
        Build a text filter object for the search API.

        Args:
            field: Field name (e.g., "DocumentType", "DocketNumber")
            value: Value to match
            operator: One of: contains, notcontains, starts, notstarts, equals, notequals

        Returns:
            Filter object for the API request
        """
        return {
            "field": field,
            "value": value,
            "operator": operator,
        }

    @staticmethod
    def build_date_filter(field: str, operator: str, date: str) -> Dict[str, str]:
        """
        Build a date filter object for the search API.

        Args:
            field: Date field name ("DocumentDate" or "DateAddedTimestamp")
            operator: One of: "ge" (on or after), "le" (on or before), "eq" (equals)
            date: Date in YYYY-MM-DD format

        Returns:
            Filter object for the API request

        Example:
            build_date_filter("DocumentDate", "ge", "2024-01-01")
            -> {"field": "DocumentDate", "value": "(DocumentDate ge '2024-01-01')"}
        """
        return {
            "field": field,
            "value": f"({field} {operator} '{date}')",
        }

    @staticmethod
    def build_date_range_filter(field: str, start_date: str, end_date: str) -> Dict[str, str]:
        """
        Build a date range (between) filter.

        Args:
            field: Date field name
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Filter object for the API request
        """
        return {
            "field": field,
            "value": f"({field} ge '{start_date}') and ({field} le '{end_date}')",
        }

    # ─────────────────────────────────────────────
    # Search API
    # ─────────────────────────────────────────────

    def search(
        self,
        query: str = "",
        filters: List[Dict[str, str]] = None,
        any_filters: List[Dict[str, str]] = None,
        main_lib: bool = True,
        legacy_lib: bool = False,
        sort: str = "DateAddedTimestamp",
        sort_direction: int = 1,  # 0 = ascending, 1 = descending
        max_results: int = 100,
        max_pages: int = 10,
        use_cache: bool = True,
    ) -> List[AdamsDocument]:
        """
        Search the ADAMS document library.

        Args:
            query: Search query text (searches content and properties)
            filters: List of filter objects (AND logic - all must match)
            any_filters: List of filter objects (OR logic - any can match)
            main_lib: Include main library (documents since Nov 1999)
            legacy_lib: Include legacy library (pre-Nov 1999)
            sort: Field to sort by (e.g., "DateAddedTimestamp", "DocumentDate")
            sort_direction: 0 = ascending, 1 = descending
            max_results: Maximum total results to return
            max_pages: Maximum pages to fetch (API returns up to 100 per page)
            use_cache: Whether to use cached results

        Returns:
            List of AdamsDocument objects
        """
        filters = filters or []
        any_filters = any_filters or []

        # Check cache
        if use_cache:
            cache_key = self._make_cache_key(
                "search",
                query=query,
                filters=filters,
                any_filters=any_filters,
                main_lib=main_lib,
                legacy_lib=legacy_lib,
                sort=sort,
                sort_direction=sort_direction,
                max_results=max_results,
            )
            cached = self._get_cache(cache_key)
            if cached is not None:
                logger.info("[search] Cache hit")
                return cached

        headers = self._get_headers()
        all_documents = []
        skip = 0

        for page in range(max_pages):
            # Build request payload
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

            if self.debug:
                logger.debug(f"[search] Page {page + 1}, payload: {json.dumps(payload, indent=2)}")

            try:
                response = self.session.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except requests.exceptions.Timeout:
                raise AdamsAPIError(f"Request timeout after {self.timeout}s")
            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error: {e}"
                try:
                    error_detail = response.json()
                    error_msg += f" - {error_detail}"
                except Exception:
                    pass
                raise AdamsAPIError(error_msg)
            except requests.RequestException as e:
                raise AdamsAPIError(f"Request failed: {e}")

            # Parse response
            data = response.json()
            results = data.get("results", [])

            if not results:
                logger.debug(f"[search] No more results on page {page + 1}")
                break

            # Convert to AdamsDocument objects
            for result in results:
                doc_data = result.get("document", {})
                doc = AdamsDocument.from_api_response(doc_data)
                all_documents.append(doc)

                if len(all_documents) >= max_results:
                    break

            if len(all_documents) >= max_results:
                break

            # Check if more pages available
            if len(results) < self.DEFAULT_PAGE_SIZE:
                break

            skip += self.DEFAULT_PAGE_SIZE

        # Trim to max_results
        all_documents = all_documents[:max_results]

        # Store search metadata
        self._last_search = {
            "query": query,
            "filters": filters,
            "any_filters": any_filters,
            "main_lib": main_lib,
            "legacy_lib": legacy_lib,
            "result_count": len(all_documents),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        # Cache results
        if use_cache:
            self._set_cache(cache_key, all_documents)

        logger.info(f"[search] Returning {len(all_documents)} documents")
        return all_documents

    def get_document(self, accession_number: str) -> Optional[AdamsDocument]:
        """
        Retrieve a single document by accession number.

        Args:
            accession_number: NRC accession number (e.g., "ML12345A678")

        Returns:
            AdamsDocument object or None if not found
        """
        if not accession_number:
            raise AdamsAPIError("Accession number is required")

        # Normalize accession number
        accession_number = accession_number.strip().upper()

        headers = self._get_headers()
        url = f"{self.base_url}/{accession_number}"

        try:
            response = self.session.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                return None
            raise AdamsAPIError(f"HTTP Error: {e}")
        except requests.RequestException as e:
            raise AdamsAPIError(f"Request failed: {e}")

        data = response.json()
        doc_data = data.get("document", data)
        return AdamsDocument.from_api_response(doc_data)

    # ─────────────────────────────────────────────
    # Search Methods
    # ─────────────────────────────────────────────

    def search_by_docket(
        self,
        docket_number: str,
        max_results: int = 100,
        days_back: int = 365,
        **kwargs,
    ) -> List[AdamsDocument]:
        """
        Search for documents by docket number.

        Args:
            docket_number: NRC docket number (e.g., "05000373")
            max_results: Maximum results to return
            days_back: How many days back to search
            **kwargs: Additional arguments passed to search()

        Returns:
            List of AdamsDocument objects
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        filters = [
            self.build_text_filter("DocketNumber", docket_number, "starts"),
            self.build_date_filter("DateAddedTimestamp", "ge", cutoff_date),
        ]

        return self.search(filters=filters, max_results=max_results, **kwargs)

    def search_by_document_type(
        self,
        document_type: str,
        query: str = "",
        max_results: int = 100,
        **kwargs,
    ) -> List[AdamsDocument]:
        """
        Search for documents by type.

        Args:
            document_type: Document type (e.g., "Inspection Report", "LER")
            query: Optional text query
            max_results: Maximum results to return
            **kwargs: Additional arguments passed to search()

        Returns:
            List of AdamsDocument objects
        """
        filters = [
            self.build_text_filter("DocumentType", document_type, "equals"),
        ]

        return self.search(query=query, filters=filters, max_results=max_results, **kwargs)

    def search_recent(
        self,
        days: int = 30,
        query: str = "",
        max_results: int = 100,
        **kwargs,
    ) -> List[AdamsDocument]:
        """
        Search for recently added documents.

        Args:
            days: Number of days back to search
            query: Optional text query
            max_results: Maximum results to return
            **kwargs: Additional arguments passed to search()

        Returns:
            List of AdamsDocument objects
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        filters = [
            self.build_date_filter("DateAddedTimestamp", "ge", cutoff_date),
        ]

        return self.search(query=query, filters=filters, max_results=max_results, **kwargs)

    # ─────────────────────────────────────────────
    # Google Search Integration
    # ─────────────────────────────────────────────

    def google_search(self, query: str, num: int = 10) -> List[Dict[str, Any]]:
        """
        Perform a Google Custom Search for NRC documents.

        Args:
            query: Search query
            num: Number of results (max 10)

        Returns:
            List of search result dictionaries with title, link, snippet
        """
        if not self.google_api_key or not self.google_cx:
            raise AdamsAPIError(
                "Google API key and CX must be set to use google_search(). "
                "Set GOOGLE_API_KEY and GOOGLE_CX environment variables."
            )

        params = {
            "key": self.google_api_key,
            "cx": self.google_cx,
            "q": query,
            "num": min(num, 10),
        }

        try:
            resp = self.session.get(self.google_base_url, params=params, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AdamsAPIError(f"Google Search request failed: {e}")

        data = resp.json()
        results = []

        for item in data.get("items", []):
            title = item.get("title")
            link = item.get("link")
            snippet = item.get("snippet")

            if not isinstance(link, str):
                continue
            if "@" in link or link.startswith("mailto:"):
                continue

            results.append({
                "title": title,
                "link": link,
                "snippet": snippet,
                "source": "Google",
            })

        return results

    # ─────────────────────────────────────────────
    # Smart Hybrid Search
    # ─────────────────────────────────────────────

    def smart_search(
        self,
        query: str,
        top_n: int = 10,
        use_google: bool = True,
        include_legacy: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid search combining ADAMS API and Google Custom Search.

        Performs relevance scoring and deduplication across both sources.

        Args:
            query: Search query
            top_n: Maximum results to return
            use_google: Whether to include Google search results
            include_legacy: Whether to include legacy library

        Returns:
            List of result dictionaries with title, link, accession_number, etc.
        """
        logger.info(f"[smart_search] query='{query}'")

        if not query or not query.strip():
            raise AdamsAPIError("Query cannot be empty.")

        combined = []
        seen_accessions = set()
        seen_links = set()

        # 1) ADAMS API search
        try:
            adams_docs = self.search(
                query=query,
                max_results=top_n * 2,
                legacy_lib=include_legacy,
            )

            for doc in adams_docs:
                acc = doc.accession_number
                if acc and acc in seen_accessions:
                    continue
                if acc:
                    seen_accessions.add(acc)

                link = doc.get_download_url()
                if link:
                    seen_links.add(link)

                combined.append({
                    "title": doc.title,
                    "link": link,
                    "accession_number": acc,
                    "document_date": doc.document_date,
                    "added_date": doc.added_date,
                    "document_type": doc.document_type,
                    "docket_number": doc.docket_number,
                    "source": "ADAMS API",
                })
        except Exception as e:
            logger.warning(f"[smart_search] ADAMS search failed: {e}")

        # 2) Google Custom Search (optional)
        if use_google and self.google_api_key and self.google_cx:
            try:
                google_results = self.google_search(
                    f"site:nrc.gov {query}",
                    num=top_n,
                )

                for g in google_results:
                    link = g.get("link")
                    if not link or link in seen_links:
                        continue
                    seen_links.add(link)

                    # Try to extract accession number from link
                    acc = None
                    if "/ML" in link:
                        try:
                            acc_start = link.index("/ML") + 1
                            acc = link[acc_start:acc_start + 12]
                        except Exception:
                            pass

                    combined.append({
                        "title": g.get("title"),
                        "link": link,
                        "accession_number": acc,
                        "snippet": g.get("snippet"),
                        "source": "Google",
                    })
            except Exception as e:
                logger.warning(f"[smart_search] Google search failed: {e}")

        # 3) Score and sort results
        query_words = [w.lower() for w in query.split() if len(w) > 2]

        def score(result):
            text = (
                (result.get("title") or "") + " " +
                (result.get("snippet") or "") + " " +
                (result.get("document_type") or "")
            ).lower()

            base_score = 0
            for word in query_words:
                if word in (result.get("title") or "").lower():
                    base_score += 3
                elif word in text:
                    base_score += 1

            # Boost ADAMS results (more authoritative)
            if result.get("source") == "ADAMS API":
                base_score += 2

            return base_score

        for result in combined:
            result["score"] = score(result)

        combined.sort(key=lambda x: x.get("score", 0), reverse=True)

        logger.info(f"[smart_search] Returning {min(top_n, len(combined))} results")
        return combined[:top_n]

    # ─────────────────────────────────────────────
    # Utility Methods
    # ─────────────────────────────────────────────

    def save_results_to_json(
        self,
        documents: List[AdamsDocument],
        filepath: str,
        include_metadata: bool = True,
    ) -> str:
        """
        Save search results to a JSON file.

        Args:
            documents: List of AdamsDocument objects
            filepath: Output file path
            include_metadata: Whether to include search metadata

        Returns:
            The filepath
        """
        output = {
            "documents": [doc.to_dict() for doc in documents],
            "count": len(documents),
        }

        if include_metadata and self._last_search:
            output["search_metadata"] = self._last_search

        with open(filepath, "w") as f:
            json.dump(output, f, indent=2)

        return filepath

    def download_documents(
        self,
        documents: List[AdamsDocument],
        directory: str = ".",
        skip_existing: bool = True,
        max_concurrent: int = 4,
    ) -> List[Dict[str, Any]]:
        """
        Download multiple documents.

        Args:
            documents: List of AdamsDocument objects
            directory: Download directory
            skip_existing: Skip files that already exist
            max_concurrent: Maximum concurrent downloads

        Returns:
            List of result dictionaries with status for each document
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        os.makedirs(directory, exist_ok=True)
        results = []

        def download_one(doc):
            try:
                path = doc.download(directory=directory, skip_existing=skip_existing)
                return {
                    "accession_number": doc.accession_number,
                    "status": "success",
                    "path": path,
                }
            except Exception as e:
                return {
                    "accession_number": doc.accession_number,
                    "status": "error",
                    "error": str(e),
                }

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures = {executor.submit(download_one, doc): doc for doc in documents}
            for future in as_completed(futures):
                results.append(future.result())

        return results


# ─────────────────────────────────────────────
# Module-level convenience function
# ─────────────────────────────────────────────

def create_client(
    api_key: str = None,
    google_api_key: str = None,
    google_cx: str = None,
    debug: bool = False,
) -> AdamsClient:
    """
    Create an ADAMS client with configuration from environment variables.

    Environment variables:
    - ADAMS_API_KEY: ADAMS API subscription key
    - GOOGLE_API_KEY: Google Custom Search API key
    - GOOGLE_CX: Google Custom Search Engine ID
    """
    return AdamsClient(
        api_key=api_key,
        google_api_key=google_api_key,
        google_cx=google_cx,
        debug=debug,
    )
