import pytest

from hermes_secure_sales.hermes import api_config_fingerprint
from hermes_secure_sales.website_knowledge import (
    WebsiteFetchError,
    _canonical_url,
    _same_origin_link,
)


def test_canonical_url_requires_public_https_domain() -> None:
    assert _canonical_url("https://Example.test/path?tracking=1#top") == (
        "https://example.test/path",
        "example.test",
    )
    for url in (
        "http://example.test/",
        "https://localhost/",
        "https://127.0.0.1/",
        "https://user:password@example.test/",
    ):
        with pytest.raises(WebsiteFetchError):
            _canonical_url(url)


def test_website_reader_keeps_same_origin_service_pages_only() -> None:
    base = "https://clinic.example.test/"
    assert _same_origin_link("/dich-vu/goi-kham", base, "clinic.example.test") == (
        "https://clinic.example.test/dich-vu/goi-kham"
    )
    assert _same_origin_link("https://elsewhere.example/test", base, "clinic.example.test") is None
    assert _same_origin_link("/benh-ly/trao-nguoc", base, "clinic.example.test") is None
    assert _same_origin_link("/cau-hoi-thuong-gap", base, "clinic.example.test") is None


def test_api_fingerprint_tracks_the_active_server_configuration() -> None:
    first = type("Settings", (), {
        "hermes_api_key": "secret-one",
        "hermes_api_url": "https://model.example.test/",
        "hermes_model": "model-a",
    })()
    same = type("Settings", (), {
        "hermes_api_key": "secret-one",
        "hermes_api_url": "https://model.example.test",
        "hermes_model": "model-a",
    })()
    changed = type("Settings", (), {
        "hermes_api_key": "secret-two",
        "hermes_api_url": "https://model.example.test",
        "hermes_model": "model-a",
    })()
    assert api_config_fingerprint(first) == api_config_fingerprint(same)
    assert api_config_fingerprint(first) != api_config_fingerprint(changed)
