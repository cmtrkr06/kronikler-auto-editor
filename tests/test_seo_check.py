import pytest

from seo_check import check, copied_spans, html_text, sanitize_html

SRC = ("Arabanızı çalıştırın sonra Siri nin açık olduğundan emin olun iPhone unuzu arabanızdaki "
       "USB kapısına bağlayın USB kapısı bir CarPlay simgesiyle etiketlenmiş olabilir")


def run(html, **kw):
    clean, removed = sanitize_html(html)
    args = dict(html=clean, seo_title="CarPlay nasıl bağlanır", meta_description="m" * 120,
                slug="carplay-nasil-baglanir", allowed_urls=["https://support.apple.com/tr-tr/108415"],
                source_texts=[SRC], claim_ids=[["K1"]], known_ids={"K1", "K2"}, removed_tags=removed)
    args.update(kw)
    return check(**args)


# ---- sanitizer
def test_sanitizer_whitelist_h1_and_attributes():
    html = ('<h1 class="x">Başlık</h1><p style="color:red" onclick="x()">Metin <b>kalın</b></p>'
            '<script>alert(1)</script><iframe src="https://e.com"></iframe><div><span>iç</span></div>'
            '<a href="https://ok.com/a" target="_blank">ok</a><a href="javascript:alert(1)">kötü</a>')
    clean, removed = sanitize_html(html)
    assert clean == ('<h2>Başlık</h2><p>Metin <b>kalın</b></p>iç'
                     '<a href="https://ok.com/a">ok</a>kötü')
    assert removed["h1->h2"] == 1 and removed["script"] == 1 and removed["a(bad href)"] == 1


def test_sanitizer_escapes_text_and_closes_unclosed_tags():
    clean, _ = sanitize_html("<p>5 < 6 & <strong>açık")
    assert clean == "<p>5 &lt; 6 &amp; <strong>açık</strong></p>"


def test_sanitizer_keeps_lists_and_tables():
    html = "<ul><li>bir</li><li>iki</li></ul><table><tr><th>a</th><td>b</td></tr></table>"
    assert sanitize_html(html)[0] == html


# ---- text helpers
def test_html_text_links_headings():
    text, links, heads = html_text('<h2>A</h2><p>b <a href="https://x.com">c</a></p><h3>d</h3>')
    assert text == "A b c d" and links == ["https://x.com"] and heads == ["h2", "h3"]


def test_copied_spans_detects_verbatim_after_normalization():
    body = "Önce şunu yapın: ARABANIZI çalıştırın, sonra Siri'nin açık olduğundan emin olun; iPhone'unuzu arabanızdaki USB kapısına bağlayın."
    assert copied_spans(body, [SRC])
    paraphrase = "Kontağı açtıktan sonra Siri etkin mi diye bakın ve telefonu CarPlay simgeli USB girişine takın."
    assert copied_spans(paraphrase, [SRC]) == []


# ---- checks
GOOD = "<h2>Kurulum</h2><p>" + " ".join(f"Adım{i} kelime{i} örnek{i} cümle{i} burada{i} yazıldı{i}." for i in range(60)) + "</p>"


def test_good_draft_passes_without_warnings():
    r = run(GOOD)
    assert r.passed and r.warnings == [] and r.word_count > 300


@pytest.mark.parametrize("html,error", [
    ("<p>Bu özellik çığır açıyor.</p>", "yasak klişe"),
    ('<p><a href="https://baska-site.com">link</a></p>', "kaynak pakette olmayan link"),
    ("<p>" + SRC + "</p>", "kaynaktan aynen alınmış"),
    ("", "gövde boş"),
])
def test_blocking_errors(html, error):
    r = run(html)
    assert not r.passed and any(error in e for e in r.errors)


def test_claim_errors():
    assert any("iddia listesi boş" in e for e in run(GOOD, claim_ids=[]).errors)
    assert any("iddia 1 kaynak göstermiyor" in e for e in run(GOOD, claim_ids=[[]]).errors)
    assert any("K9" in e for e in run(GOOD, claim_ids=[["K1", "K9"]]).errors)


def test_turkish_cliche_case_insensitive():
    # str.casefold("İ") gives "i̇" (i + combining dot): the check must use Turkish lowercasing
    assert any("devrim niteliğinde" in e for e in run("<p>DEVRİM NİTELİĞİNDE bir güncelleme</p>").errors)
    assert any("çığır açıyor" in e for e in run("<p>ÇIĞIR AÇIYOR</p>").errors)


def test_warnings_lengths_slug_headings_density_and_removed():
    stuffed = "<h1>X</h1><p>" + "carplay " * 50 + "ve diğer kelimeler burada " * 30 + "</p>"
    r = run(stuffed, seo_title="x" * 70, meta_description="kısa", slug="Geçersiz Slug")
    joined = " | ".join(r.warnings)
    for part in ("SEO başlığı 70", "meta açıklama 4", "slug biçimi", "kısa:", "'carplay' kelimesi çok sık",
                 "h1->h2"):
        assert part in joined, part
    assert r.passed  # warnings never block


# ---- zapyus project rules (2026-10-14)
def _guide(body, title="Škoda MIB3 Kablosuz CarPlay Kopuyor: Profil Sıfırlama Çözümü"):
    from seo_check import check
    return check(html=body, seo_title="t", meta_description="m" * 100, slug="a-b", allowed_urls=[],
                 source_texts=[], claim_ids=[["K1"]], known_ids={"K1"}, kind="guide", title=title)


GOOD_GUIDE = ("<p>Kablosuz CarPlay kopuyorsa profili silin.</p><h2>Teşhis</h2><table><tr><th>Belirti</th>"
              "<th>Olası Neden</th><th>Hızlı Çözüm</th></tr><tr><td>a</td><td>b</td><td>c</td></tr></table>"
              "<h2>Adımlar</h2><p>Metin.</p><blockquote><p><strong>Pratik Not:</strong> ipucu.</p></blockquote>")


def test_good_guide_structure_passes():
    assert _guide(GOOD_GUIDE).errors == []


def test_guide_structure_rules():
    r = _guide("<h2>Giriş</h2><p>Sonuç olarak kusursuz. Test ettiğimizde çalıştı.</p><h2>Sonuç</h2><p>x</p>",
               title="x" * 70)
    joined = " | ".join(r.errors)
    for part in ("başlık 70", "tablo yok", "Pratik Not", "giriş paragrafıyla", "sonuç olarak", "kusursuz",
                 "uydurma deneyim", "'sonuç' başlığı"):
        assert part in joined, part


def test_structure_rules_only_for_guides_but_honesty_rules_for_all():
    from seo_check import check
    r = check(html="<p>Haberi denedik.</p>", seo_title="t", meta_description="m" * 100, slug="a",
              allowed_urls=[], source_texts=[], claim_ids=[["K1"]], known_ids={"K1"}, kind="news")
    assert [e for e in r.errors if "uydurma deneyim" in e] and not [e for e in r.errors if "tablo" in e]


def test_third_person_user_reports_are_not_fake_experience():
    assert _guide(GOOD_GUIDE.replace("ipucu.", "Kullanıcıların denediği yöntem bildiriliyor.")).errors == []
