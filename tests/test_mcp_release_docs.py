"""Keep issuer setup instructions and current release identity aligned."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def test_current_issuer_guides_describe_the_implemented_contract():
    for name in ("README.md", "MCP_TESTING.md", "MCP_ISSUER_TESTING.md",
                 "docs/docs/chatgpt.html"):
        text = (ROOT / name).read_text()
        assert "authorization_response_iss_parameter_supported" in text, name
        assert "TIMEBOARDAPP_MCP_REDIRECT_URIS" in text, name
        assert "ChatGPT" in text and "exact" in text, name
    guide = (ROOT / "docs/docs/chatgpt.html").read_text()
    assert 'id="issuer-identification"' in guide
    assert 'id="troubleshooting"' in guide
    assert "curl.exe" in guide
    assert "invalid_redirect_uri" in guide


def test_current_installation_guides_select_the_authoritative_patch():
    version = runpy.run_path(str(ROOT / "app/version.py"))["APP_VERSION"]
    for name in ("README.md", "MCP_TESTING.md", "MCP_ISSUER_TESTING.md",
                 "docs/docs/deployment.html", "docs/docs/getting-started.html"):
        assert "v" + version in (ROOT / name).read_text(), name
    assert version in (ROOT / "app/templates/help.html").read_text()
    assert version in (ROOT / "docs/README.md").read_text()
