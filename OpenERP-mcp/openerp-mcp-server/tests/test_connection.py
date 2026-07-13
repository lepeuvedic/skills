"""Tests for HTTPS enforcement in the connection layer (no live server)."""

import pytest

from openerp_mcp.connection import (
    HttpsXmlRPCSConnector,
    HttpsJsonRPCSConnector,
    InsecureTransportError,
)


def test_xmlrpcs_url_is_https_with_default_port():
    c = HttpsXmlRPCSConnector("erp.example.com", 443)
    assert c.url == "https://erp.example.com:443/xmlrpc"


def test_xmlrpcs_url_honours_base_path():
    c = HttpsXmlRPCSConnector("erp.example.com", 443, base_path="erp")
    assert c.url == "https://erp.example.com:443/erp/xmlrpc"
    c2 = HttpsXmlRPCSConnector("erp.example.com", 443, base_path="/erp/")
    assert c2.url == "https://erp.example.com:443/erp/xmlrpc"


def test_jsonrpcs_url_is_https():
    c = HttpsJsonRPCSConnector("erp.example.com", 8443, base_path="api")
    assert c.url == "https://erp.example.com:8443/api/jsonrpc"


def test_xmlrpcs_send_refuses_non_https_url():
    c = HttpsXmlRPCSConnector("erp.example.com", 443)
    c.url = "http://erp.example.com:80/xmlrpc"  # tamper
    with pytest.raises(InsecureTransportError):
        c.send("common", "login", "db", "user", "pass")


def test_jsonrpcs_send_refuses_non_https_url():
    c = HttpsJsonRPCSConnector("erp.example.com", 443)
    c.url = "http://erp.example.com/jsonrpc"  # tamper
    with pytest.raises(InsecureTransportError):
        c.send("object", "execute", "db")
