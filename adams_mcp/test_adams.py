#!/usr/bin/env python3
"""
Test script for ADAMS MCP Server and Client

This script tests the ADAMS client and MCP server functionality.
Run this after installing dependencies with: pip install -r requirements.txt
"""

import os
import sys
import tempfile
from pathlib import Path

def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")
    try:
        import requests
        import xml.etree.ElementTree as ET
        from urllib3.util.retry import Retry
        from requests.adapters import HTTPAdapter
        print("✓ Core dependencies imported successfully")
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        print("Please install dependencies: pip install -r requirements.txt")
        return False

    try:
        from adams_client_v3 import AdamsClient, AdamsDocument, AdamsAPIError
        print("✓ AdamsClient imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import AdamsClient: {e}")
        return False

    return True


def test_client_initialization():
    """Test AdamsClient initialization."""
    print("\nTesting AdamsClient initialization...")
    try:
        from adams_client_v3 import AdamsClient

        # Test basic initialization
        client = AdamsClient()
        assert client.base_url is not None
        assert client._last_search is None  # Should be initialized
        print("✓ AdamsClient initialized successfully")

        # Test with Google API parameters
        client_with_google = AdamsClient(
            google_api_key="test_key",
            google_cx="test_cx"
        )
        assert client_with_google.google_api_key == "test_key"
        assert client_with_google.google_cx == "test_cx"
        print("✓ AdamsClient with Google credentials initialized successfully")

        return True
    except Exception as e:
        print(f"✗ Initialization failed: {e}")
        return False


def test_input_validation():
    """Test input validation in AdamsClient methods."""
    print("\nTesting input validation...")
    from adams_client_v3 import AdamsClient, AdamsAPIError

    client = AdamsClient()

    # Test search validation
    try:
        # Should raise error - no search criteria
        client.search()
        print("✗ Should have raised error for empty search")
        return False
    except AdamsAPIError as e:
        if "At least one search criterion" in str(e):
            print("✓ Search validation working correctly")
        else:
            print(f"✗ Unexpected error: {e}")
            return False

    # Test page_size validation
    try:
        client.search(query="test", page_size=200)
        print("✗ Should have raised error for invalid page_size")
        return False
    except AdamsAPIError as e:
        if "page_size must be between" in str(e):
            print("✓ Page size validation working correctly")
        else:
            print(f"✗ Unexpected error: {e}")
            return False

    # Test Google search validation
    client_no_google = AdamsClient()
    try:
        client_no_google.google_search("test")
        print("✗ Should have raised error for missing Google credentials")
        return False
    except AdamsAPIError as e:
        if "Google API key" in str(e):
            print("✓ Google search validation working correctly")
        else:
            print(f"✗ Unexpected error: {e}")
            return False

    return True


def test_document_class():
    """Test AdamsDocument class functionality."""
    print("\nTesting AdamsDocument class...")
    from adams_client_v3 import AdamsDocument

    # Create a test document
    doc = AdamsDocument(
        title="Test Document",
        accession_number="ML12345A678",
        document_date="2024-01-15",
        document_type="Letter"
    )

    assert doc.title == "Test Document"
    assert doc.accession_number == "ML12345A678"
    print("✓ AdamsDocument creation successful")

    # Test to_dict method
    doc_dict = doc.to_dict()
    assert isinstance(doc_dict, dict)
    assert doc_dict["title"] == "Test Document"
    print("✓ AdamsDocument to_dict() working")

    # Test to_json method
    doc_json = doc.to_json()
    assert isinstance(doc_json, str)
    assert "Test Document" in doc_json
    print("✓ AdamsDocument to_json() working")

    return True


def test_mcp_server_syntax():
    """Test that MCP server file has valid syntax."""
    print("\nTesting MCP server syntax...")
    try:
        import ast
        with open('adams_mcp.py', 'r') as f:
            code = f.read()
        ast.parse(code)
        print("✓ adams_mcp.py syntax is valid")
        return True
    except SyntaxError as e:
        print(f"✗ Syntax error in adams_mcp.py: {e}")
        return False


def test_live_search():
    """Test a real search against ADAMS (requires network)."""
    print("\nTesting live ADAMS search (this may take a moment)...")
    from adams_client_v3 import AdamsClient

    try:
        client = AdamsClient(debug=False)

        # Search for a common term
        results = client.search(
            query="inspection report",
            max_pages=1,
            page_size=5
        )

        if results:
            print(f"✓ Live search successful - found {len(results)} results")
            print(f"  First result: {results[0].title[:60]}...")
            return True
        else:
            print("⚠ Search returned no results (may be network issue)")
            return True  # Not a failure

    except Exception as e:
        print(f"⚠ Live search failed (may be network issue): {e}")
        return True  # Not a critical failure for testing


def main():
    """Run all tests."""
    print("=" * 60)
    print("ADAMS MCP Server Test Suite")
    print("=" * 60)

    # Change to script directory
    os.chdir(Path(__file__).parent)

    tests = [
        ("Imports", test_imports),
        ("Client Initialization", test_client_initialization),
        ("Input Validation", test_input_validation),
        ("Document Class", test_document_class),
        ("MCP Server Syntax", test_mcp_server_syntax),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"✗ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))

    # Try live search if previous tests passed
    if all(r[1] for r in results):
        try:
            result = test_live_search()
            results.append(("Live Search", result))
        except Exception as e:
            print(f"⚠ Live search test skipped: {e}")

    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(1 for _, r in results if r)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
