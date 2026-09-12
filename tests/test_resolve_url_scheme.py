"""Citation URLs are data, so _resolve_url must only ever probe http(s).

Negative control: a file:// URL returns False without reaching urlopen. Positive
control in the same test: an https URL is probed and a 200 reads as resolved, so a
harness that never calls urlopen cannot pass as "blocked".
"""

import scripts.build_trait_rubrics as b


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_resolve_url_refuses_non_http_schemes_and_probes_https(monkeypatch):
    opened = []

    def fake_urlopen(req, timeout):
        opened.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(b._urlrequest, "urlopen", fake_urlopen)
    assert b._resolve_url("https://doi.org/10.1000/x") is True
    assert b._resolve_url("file:///etc/passwd") is False
    assert opened == ["https://doi.org/10.1000/x"]
