import os
import io
import json
import logging
from datetime import datetime
from urllib.parse import quote, urlencode

import requests
import xml.etree.ElementTree as ET
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

#   Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AdamsAPIError(Exception):
    """Custom exception for errors related to ADAMS API requests or processing."""
    pass


class AdamsDocument:
    """Represents a document record returned by an ADAMS search, with associated metadata."""
    def __init__(self, title=None, accession_number=None, document_date=None, added_date=None,
                 document_type=None, author_name=None, author_affiliation=None,
                 addressee_name=None, addressee_affiliation=None,
                 docket_number=None, license_number=None, package_number=None,
                 document_report_number=None, keywords=None,
                 page_count=None, content_size=None, mime_type=None, uri=None):

        self.title = title
        self.accession_number = accession_number
        self.document_date = document_date
        self.added_date = added_date
        self.document_type = document_type
        self.author_name = author_name
        self.author_affiliation = author_affiliation
        self.addressee_name = addressee_name
        self.addressee_affiliation = addressee_affiliation
        self.docket_number = docket_number
        self.license_number = license_number
        self.package_number = package_number
        self.document_report_number = document_report_number
        self.keywords = keywords

        try:
            self.page_count = int(page_count) if page_count not in (None, "", "None") else None
        except ValueError:
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

    def to_dict(self):
        """Return the document's metadata as a dictionary."""
        return {
            "title": self.title,
            "accession_number": self.accession_number,
            "document_date": self.document_date,
            "added_date": self.added_date,
            "document_type": self.document_type,
            "author_name": self.author_name,
            "author_affiliation": self.author_affiliation,
            "addressee_name": self.addressee_name,
            "addressee_affiliation": self.addressee_affiliation,
            "docket_number": self.docket_number,
            "license_number": self.license_number,
            "package_number": self.package_number,
            "document_report_number": self.document_report_number,
            "keywords": self.keywords,
            "page_count": self.page_count,
            "content_size": self.content_size,
            "mime_type": self.mime_type
        }

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2)

    def download(self, directory=".", filename=None, skip_existing=True):
        """Download the document's file (PDF) to the specified directory."""
        if not self.accession_number:
            raise AdamsAPIError("No accession number available; cannot download.")

        acc = self.accession_number
        if acc.startswith("ML"):
            folder = acc[:6]
            download_url = f"https://pbadupws.nrc.gov/docs/{folder}/{acc}.pdf"
        elif self.uri:
            download_url = self.uri
        else:
            raise AdamsAPIError(f"Cannot determine download URL for accession: {acc}")

        os.makedirs(directory, exist_ok=True)
        filename = filename or f"{acc}.pdf"
        file_path = os.path.join(directory, filename)

        if skip_existing and os.path.exists(file_path):
            logger.debug("Skipping existing file: %s", file_path)
            return file_path

        try:
            response = requests.get(download_url, stream=True, timeout=20)
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
        return f"<AdamsDocument {self.accession_number or ''}>"


class AdamsClient:
    """Client for NRC ADAMS API."""
    def __init__(self, base_url=None, debug=False, google_api_key=None, google_cx=None):
        self.base_url = base_url or "https://adams.nrc.gov/wba/services/search/advanced/nrc"
        self.debug = debug
        self.session = requests.Session()

        # Retry strategy
        retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))

        # Google API Integration
        self.google_api_key = google_api_key
        self.google_cx = google_cx
        self.google_base_url = "https://www.googleapis.com/customsearch/v1"

        # Initialize search context
        self._last_search = None

    # Core Methods

    # Google Search
    def google_search(self, query, num=10):
        """
        Perform a Google search using Google's Custom Search API.

        Args:
            query: Search query string
            num: Number of results to return (1-10, default: 10)

        Returns:
            List of dictionaries with 'title', 'link', and 'snippet' keys

        Raises:
            AdamsAPIError: If API credentials are missing or the request fails
        """
        # Input validation
        if not self.google_api_key or not self.google_cx:
            raise AdamsAPIError("Google API key and CX must be set to use google_search().")
        if not query or not query.strip():
            raise AdamsAPIError("Query cannot be empty")
        if num < 1 or num > 10:
            raise AdamsAPIError("num must be between 1 and 10")

        params = {
            "key": self.google_api_key,
            "cx": self.google_cx,
            "q": query,
            "num": num
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
                "snippet": snippet
            })
        return results
    
    def _build_query_params(self, query, library, filters, folder_path, added_this_month, added_today, combine):
        if library.lower() not in ("public", "legacy"):
            raise AdamsAPIError("library must be 'public' or 'legacy'")
        lib_flag = "public-library" if library.lower() == "public" else "legacy-library"
        if added_this_month and added_today:
            raise AdamsAPIError("added_this_month and added_today cannot both be True.")

        sections = [f"filters:({lib_flag}:!t)"]

        # Folder/date filters
        if folder_path or added_this_month or added_today:
            if folder_path:
                path = folder_path
                insub = ',' not in path
            else:
                now = datetime.now()
                month_folder = now.strftime("%B %Y")
                if added_today:
                    day_folder = now.strftime("%B %d, %Y")
                    path = f"/Recent Released Documents/{month_folder}/{day_folder}"
                    insub = False
                else:
                    path = f"/Recent Released Documents/{month_folder}"
                    insub = True
            path_enc = path.replace(' ', '+')
            insub_flag = "!t" if insub else "!f"
            sections.append(f"options:(within-folder:(enable:!t,insubfolder:{insub_flag},path:'{path_enc}'))")

        cond_all, cond_any = [], []
        if filters:
            filter_items = []
            if isinstance(filters, dict):
                for field, val in filters.items():
                    if isinstance(val, list):
                        filter_items.append((field, "INLIST", val))
                    else:
                        filter_items.append((field, "eq", val))
            elif isinstance(filters, list):
                for item in filters:
                    if not isinstance(item, (tuple, list)) or not (2 <= len(item) <= 3):
                        raise AdamsAPIError("filters list items must be 2- or 3-tuple")
                    if len(item) == 3:
                        field, op, val = item
                        if isinstance(val, list):
                            filter_items.append((field, "INLIST", val))
                        else:
                            filter_items.append((field, op, val))
                    else:
                        field, val = item
                        if isinstance(val, list):
                            filter_items.append((field, "INLIST", val))
                        else:
                            filter_items.append((field, "eq", val))
            else:
                raise AdamsAPIError("filters must be a dict or a list of tuples")

            combine_mode = combine.strip().upper()
            if combine_mode not in ("AND", "OR"):
                raise AdamsAPIError("combine must be 'AND' or 'OR'")

            if combine_mode == "OR":
                for field, op, val in filter_items:
                    if op == "INLIST":
                        for v in val:
                            cond_any.append(f"!({field},eq,'{quote(str(v))}','')")
                    else:
                        cond_any.append(f"!({field},{op},'{quote(str(val))}','')")
            else:
                multi_filter = None
                for item in filter_items:
                    if item[1] == "INLIST":
                        if multi_filter:
                            raise AdamsAPIError("multiple list filters not supported with AND logic")
                        multi_filter = item
                if multi_filter:
                    field, _, values = multi_filter
                    for v in values:
                        cond_any.append(f"!({field},eq,'{quote(str(v))}','')")
                    filter_items = [it for it in filter_items if it != multi_filter]
                for field, op, val in filter_items:
                    if op != "INLIST":
                        cond_all.append(f"!({field},{op},'{quote(str(val))}','')")

        if cond_all:
            sections.append(f"properties_search_all:!({','.join(cond_all)})")
        if cond_any:
            sections.append(f"properties_search_any:!({','.join(cond_any)})")

        if query:
            sections.append(f"single_content_search:'{quote(query)}'")

        q_param = f"(mode:sections,sections:({','.join(sections)}))"
        return {"q": q_param, "qn": "AdamsSearch", "tab": "advanced-search-pars"}

    def _parse_results(self, xml_str):
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            raise AdamsAPIError("Received invalid XML from ADAMS API")

        results = []
        for res_elem in root.findall(".//result"):
            rec = {child.tag: child.text for child in res_elem}
            doc = AdamsDocument(
                title=rec.get("DocumentTitle"),
                accession_number=rec.get("AccessionNumber"),
                document_date=rec.get("DocumentDate"),
                added_date=rec.get("PublishDatePARS"),
                document_type=rec.get("DocumentType"),
                author_name=rec.get("AuthorName"),
                author_affiliation=rec.get("AuthorAffiliation"),
                addressee_name=rec.get("AddresseeName"),
                addressee_affiliation=rec.get("AddresseeAffiliation"),
                docket_number=rec.get("DocketNumber"),
                license_number=rec.get("LicenseNumber"),
                package_number=rec.get("PackageNumber"),
                document_report_number=rec.get("DocumentReportNumber"),
                keywords=rec.get("Keyword"),
                page_count=rec.get("EstimatedPageCount"),
                content_size=rec.get("ContentSize"),
                mime_type=rec.get("MimeType"),
                uri=rec.get("URI")
            )
            results.append(doc)
        return results

    def search(self, query=None, library="public", filters=None, folder_path=None,
               added_this_month=False, added_today=False, combine="AND",
               page_size=50, max_pages=1, stop_when_no_results=True):
        """
        Search the NRC ADAMS database with flexible filtering options.

        Args:
            query: Search text to find in document content (optional if filters provided)
            library: Which ADAMS library to search - 'public' or 'legacy' (default: 'public')
            filters: Dictionary or list of field filters (e.g., {'AccessionNumber': 'ML12345A678'})
            folder_path: Specific ADAMS folder path to search within
            added_this_month: Filter to documents added this month
            added_today: Filter to documents added today
            combine: How to combine filters - 'AND' or 'OR' (default: 'AND')
            page_size: Results per page (1-100, default: 50)
            max_pages: Maximum pages to retrieve (1-100, default: 1)
            stop_when_no_results: Stop pagination when a page returns no results

        Returns:
            List of AdamsDocument objects matching the search criteria

        Raises:
            AdamsAPIError: If the search request fails or parameters are invalid
        """
        # Input validation
        if page_size < 1 or page_size > 100:
            raise AdamsAPIError("page_size must be between 1 and 100")
        if max_pages < 1 or max_pages > 100:
            raise AdamsAPIError("max_pages must be between 1 and 100")
        if not query and not filters and not folder_path and not added_this_month and not added_today:
            raise AdamsAPIError("At least one search criterion must be provided (query, filters, or date/folder filter)")

        params_base = self._build_query_params(query, library, filters, folder_path,
                                               added_this_month, added_today, combine)

        all_results = []
        for page in range(max_pages):
            params = dict(params_base)
            params["start"] = page * page_size
            params["rows"] = page_size

            if self.debug:
                logger.debug("Fetching page %d: %s", page + 1, urlencode(params))

            try:
                resp = self.session.get(self.base_url, params=params, timeout=20)
                resp.raise_for_status()
            except requests.RequestException as e:
                raise AdamsAPIError(f"Request failed: {e}")

            batch = self._parse_results(resp.text)
            if not batch and stop_when_no_results:
                break

            all_results.extend(batch)

        self._last_search = {
            "query": query,
            "library": library,
            "filters": filters,
            "folder_path": folder_path,
            "added_this_month": added_this_month,
            "added_today": added_today,
            "combine": combine,
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z"
        }
        return all_results

    # ──────────────────────────────────────────────────────────────
    #  Export helpers
    # ──────────────────────────────────────────────────────────────
    def save_results_to_json(self, documents, file_path=None):
        if not hasattr(self, "_last_search"):
            raise AdamsAPIError("No search context available to save.")

        if file_path is None:
            q = (self._last_search.get("query") or "search").strip()
            stem = "_".join(q.split())[:30] or "search"
            ts = self._last_search["timestamp"].replace(":", "").replace("-", "")
            file_path = f"{stem}_{ts}.json"

        payload = {
            "search": self._last_search,
            "results": [doc.to_dict() for doc in documents]
        }
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            raise AdamsAPIError(f"Could not write to {file_path}: {e}")
        return file_path

    def download_all(self, documents, directory=".", skip_existing=True, max_workers=4):
        from concurrent.futures import ThreadPoolExecutor
        os.makedirs(directory, exist_ok=True)

        def _download(doc):
            try:
                return doc.download(directory=directory, skip_existing=skip_existing)
            except AdamsAPIError as e:
                logger.error("Failed to download %s: %s", doc.accession_number, e)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            return list(executor.map(_download, documents))
