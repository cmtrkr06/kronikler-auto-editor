import pytest

from normalizer import (content_hash, normalize_title, normalize_url, strip_html, topic_key,
                        turkish_lower)


# ---- URL normalization
@pytest.mark.parametrize("a,b", [
    ("HTTP://WWW.Example.COM/Path/", "https://example.com/Path"),
    ("https://example.com/a?utm_source=x&b=2&a=1#frag", "https://example.com/a?a=1&b=2"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "https://example.com/a"),
    ("https://example.com//a///b/", "https://example.com/a/b"),
    ("https://example.com/", "https://example.com"),
    ("example.com/x", "https://example.com/x"),
    ("https://example.com/a?fbclid=1&gclid=2", "https://example.com/a"),
    ("  https://example.com/a  ", "https://example.com/a"),
])
def test_normalize_url(a, b):
    assert normalize_url(a) == b


def test_url_keeps_nondefault_port_and_meaningful_query():
    assert normalize_url("https://example.com:8443/a?id=5") == "https://example.com:8443/a?id=5"


def test_url_equivalent_forms_collapse():
    forms = ["https://www.macrumors.com/2026/10/01/x/", "http://macrumors.com/2026/10/01/x",
             "https://macrumors.com/2026/10/01/x?utm_campaign=y#top"]
    assert len({normalize_url(f) for f in forms}) == 1


@pytest.mark.parametrize("bad", ["", "   ", None, "mailto:a@b.com", "javascript:alert(1)",
                                 "https://", "http://exa mple.com:99999999"])
def test_url_unusable(bad):
    assert normalize_url(bad) == ""


def test_url_turkish_path_is_stable():
    a = normalize_url("https://example.com/haber/şkoda-yeni")
    b = normalize_url("https://example.com/haber/%C5%9Fkoda-yeni")
    assert a == b


# ---- title normalization / Türkçe karakterler
def test_turkish_lower_dotted_dotless_i():
    assert turkish_lower("İSTANBUL") == "istanbul"
    assert turkish_lower("ISPARTA") == "ısparta"


@pytest.mark.parametrize("a,b", [
    ("ŞKODA Kodiaq: Yeni Kasa!", "skoda kodiaq yeni kasa"),
    ("İşte Yeni CarPlay Özellikleri", "iste yeni carplay ozellikleri"),
    ("  Apple   CarPlay'e\tyeni   özellikler  ", "apple carplay e yeni ozellikler"),
    ("ÇOK ÖNEMLİ ĞÜŞİÖÇ", "cok onemli gusioc"),
])
def test_normalize_title(a, b):
    assert normalize_title(a) == b


def test_title_variants_match():
    assert normalize_title("Skoda Kodiaq – yeni nesil") == normalize_title("ŞKODA KODİAQ - YENİ NESİL")
    assert normalize_title("Apple’s CarPlay") == normalize_title("Apple's CarPlay")
    assert normalize_title("Café") == normalize_title("cafe")


def test_title_decomposed_unicode_equals_composed():
    assert normalize_title("I\u0307stanbul") == normalize_title("İstanbul")


def test_title_html_entities_and_empty():
    assert normalize_title("Apple &amp; VW") == "apple vw"
    assert normalize_title("") == "" and normalize_title(None) == ""
    assert normalize_title("!!! ???") == ""


def test_title_keeps_non_latin_letters():
    assert normalize_title("任天堂 Switch 2") != ""


def test_strip_html():
    assert strip_html("<p>Merhaba&nbsp;<b>dünya</b></p>\n") == "Merhaba dünya"


# ---- hash
def test_hash_deterministic_and_normalized():
    assert content_hash("Merhaba Dünya") == content_hash("  merhaba   DÜNYA! ")
    assert content_hash("a") != content_hash("b")
    assert len(content_hash("x")) == 64


def test_topic_key_folds_turkish():
    assert topic_key("Arıza: DQ200 Şanzıman") == "ariza-dq200-sanziman"
