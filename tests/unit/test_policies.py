import pytest

from agent_harness.domain.conversation import Intent
from agent_harness.domain.errors import PolicyDenied
from agent_harness.domain.policies import ExportPolicy, SourcePolicy


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://100.64.0.1/",
        "http://0.0.0.0/",
        "http://printer.local/",
        "http://db.internal/",
        "http://user:pass@example.com/",
        "not a url",
    ],
)
def test_source_policy_blocks_internal_and_unsafe_urls(url):
    with pytest.raises(PolicyDenied):
        SourcePolicy().check_url(url)


@pytest.mark.parametrize("url", ["https://github.com/acme/tiny-api", "http://blog.example.com/post", "https://8.8.8.8/"])
def test_source_policy_allows_public_urls(url):
    SourcePolicy().check_url(url)


@pytest.mark.parametrize("ip", ["10.1.2.3", "172.16.0.1", "127.0.0.1", "169.254.169.254", "fd00::1", "::1", "224.0.0.1"])
def test_source_policy_blocks_private_resolved_ips(ip):
    with pytest.raises(PolicyDenied):
        SourcePolicy().check_ip(ip)


def test_allowlist_restricts_hosts_and_accepts_subdomains():
    policy = SourcePolicy(allowed_hosts=frozenset({"github.com"}))
    policy.check_url("https://api.github.com/repos/a/b")
    with pytest.raises(PolicyDenied):
        policy.check_url("https://example.com/")


@pytest.mark.parametrize(
    ("mode", "explicit", "expected"),
    [("explicit", True, True), ("explicit", False, False), ("deny", True, False), ("allow", False, True)],
)
def test_export_policy(mode, explicit, expected):
    assert ExportPolicy(mode).allows(Intent(explicit_export=explicit)) is expected
