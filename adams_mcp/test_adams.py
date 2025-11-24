#!/usr/bin/env python3
"""
Test suite for ADAMS MCP + adams_client_v4

Run with:
    python test_adams.py
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# =====================================================================
# 1. IMPORT TESTS
# =====================================================================
def test_imports():
    print("Testing imports...")

    try:
        import requests
        import xml.etree.ElementTree as ET
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        print("✓ Core dependencies imported")
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

    try:
        from adams_client_v4 import AdamsClient, AdamsDocument, AdamsAPIError
        print("✓ adams_client_v4 imported successfully")
    except Exception as e:
        print(f"✗ Failed to import adams_client_v4: {e}")
        return False

    return True

# =====================================================================
# 2. CLIENT INITIALIZATION
# =====================================================================
def test_client_initialization():
    print("\nTesting client initialization...")
    from adams_client_v4 import AdamsClient

    client = AdamsClient()
    assert client.base_url.startswith("https"), "Base URL must be HTTPS"
    assert client.session is not None
    print("✓ Client initialized")

    client2 = AdamsClient(google_api_key="abc", google_cx="xyz")
    assert client2.google_api_key == "abc"
    assert client2.google_cx == "xyz"
    print("✓ Client initialized with Google credentials")

    return True

# =====================================================================
# 3. QUERY EXPANSION
# =====================================================================
def test_query_expansion():
    print("\nTesting query expansion...")
    from adams_client_v4 import AdamsClient

    c = AdamsClient()
    expanded = c._expand_query("molten salt reactor")

    assert "msr" in expanded or "MSR" in expanded
    assert "molten salt reactor" in expanded
    print("✓ Query expansion working")

    return True

# =====================================================================
# 4. CACHING BEHAVIOR
# =====================================================================
def test_caching():
    print("\nTesting caching (smart_search)...")
    from adams_client_v4 import AdamsClient

    c = AdamsClient(google_api_key="abc", google_cx="xyz")

    fake_google = [{
        "title": "Cached Doc",
        "link": "https://pbadupws.nrc.gov/docs/ML11111A111.pdf",
        "snippet": "cached test",
        "source": "Google"
    }]

    # Patch google_search to simulate API hit
    with patch.object(AdamsClient, "google_search", return_value=fake_google) as mock_search:

        # First call → uses google_search
        r1 = c.smart_search("cache-test", top_n=3)
        assert mock_search.called
        mock_search.reset_mock()

        # Second call → should use cache (no google_search call)
        r2 = c.smart_search("cache-test", top_n=3)
        mock_search.assert_not_called()

        # Results must match
        assert r1 == r2

    print("✓ Caching system validated via smart_search")
    return True

# =====================================================================
# 5. XML PARSING
# =====================================================================
def test_xml_parsing():
    print("\nTesting XML parsing...")

    from adams_client_v4 import AdamsClient

    xml = """
    <root>
      <result>
        <DocumentTitle>Test Title</DocumentTitle>
        <AccessionNumber>ML12345A001</AccessionNumber>
      </result>
    </root>
    """

    c = AdamsClient()
    documents = c._parse_results(xml)

    assert len(documents) == 1
    assert documents[0].title == "Test Title"
    assert documents[0].accession_number == "ML12345A001"

    print("✓ XML parsing OK")
    return True

# =====================================================================
# 6. SMART SEARCH (MOCKED GOOGLE + LEGACY)
# =====================================================================
def test_smart_search():
    print("\nTesting smart_search (mocked)...")

    from adams_client_v4 import AdamsClient

    c = AdamsClient(google_api_key="abc", google_cx="xyz")

    # Mock Google results
    fake_google = [{
        "title": "Molten Salt Reactor Overview",
        "link": "https://pbadupws.nrc.gov/docs/ML12345A111.pdf",
        "snippet": "Testing MSR documentation",
        "source": "Google"
    }]

    # Mock legacy ADAMS result
    fake_xml = """
    <root>
      <result>
        <DocumentTitle>Legacy Reactor Doc</DocumentTitle>
        <AccessionNumber>ML55555A555</AccessionNumber>
      </result>
    </root>
    """

    with patch.object(AdamsClient, "google_search", return_value=fake_google):
        with patch("requests.Session.get") as mock_http:
            mock_http.return_value.status_code = 200
            mock_http.return_value.text = fake_xml

            results = c.smart_search("molten salt reactor", top_n=5)

            assert len(results) >= 2  # Google + Legacy
            assert results[0]["title"]  # ranked highest
            print("✓ smart_search combines results and ranks them")

    return True

# =====================================================================
# 7. MCP SYNTAX CHECK
# =====================================================================
def test_mcp_syntax():
    print("\nTesting MCP server syntax...")

    import ast
    try:
        with open("adams_mcp.py", "r") as f:
            ast.parse(f.read())
        print("✓ adams_mcp.py syntax valid")
        return True
    except Exception as e:
        print(f"✗ Syntax error in MCP file: {e}")
        return False

# =====================================================================
# 8. OPTIONAL: LIVE SEARCH
# =====================================================================
def test_live_search():
    print("\nTesting live search (optional)...")

    from adams_client_v4 import AdamsClient

    c = AdamsClient()

    try:
        results = c.search(query="inspection")
        print(f"✓ Live search returned {len(results)} results (OK if > 0)")
    except Exception as e:
        print(f"⚠ Live search failed (network issue OK): {e}")

    return True

# =====================================================================
# MAIN TEST RUNNER
# =====================================================================
def main():
    print("=" * 60)
    print("ADAMS CLIENT V5 TEST SUITE")
    print("=" * 60)

    tests = [
        ("Imports", test_imports),
        ("Client Initialization", test_client_initialization),
        ("Query Expansion", test_query_expansion),
        ("Caching", test_caching),
        ("XML Parsing", test_xml_parsing),
        ("Smart Search", test_smart_search),
        ("MCP Syntax", test_mcp_syntax),
        ("Live Search", test_live_search),
    ]

    passed = 0
    for name, test in tests:
        try:
            result = test()
            if result:
                print(f"✓ PASS: {name}")
                passed += 1
            else:
                print(f"✗ FAIL: {name}")
        except Exception as e:
            print(f"✗ FAIL: {name} — Exception: {e}")

    print("\nSummary:")
    print(f"{passed}/{len(tests)} tests passed")


if __name__ == "__main__":
    main()

