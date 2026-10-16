from config import ImageSettings
from image_library import (LibraryImage, best_match, label_new, large, load, merge, parse_archive, refresh, save)
from models import FetchOutcome, FetchResult

B = "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEabc"
ARCHIVE = f"""
<div class="separator"><a href="{B}1/s1600/golf-8-kirmizi.jpg" style="margin-left:1em">
<img border="0" data-original-height="1080" height="180" src="{B}1/s320/golf-8-kirmizi.jpg" width="320" /></a></div>
<p><img alt="Škoda Octavia RS beyaz" src="{B}2/w640-h360-rw/IMG_1234.jpeg" /></p>
<a href="{B}1/s1600/golf-8-kirmizi.jpg"><img src="{B}1/s400/golf-8-kirmizi.jpg"></a>
<img src="https://example.com/not-blogger.jpg" alt="Golf">
<img src="{B}3/s1600/IMG_9999.jpg">
"""


def test_archive_parse_large_size_unique_blogger_only():
    imgs = parse_archive(ARCHIVE)
    assert [i.url for i in imgs] == [f"{B}1/s1600/golf-8-kirmizi.jpg", f"{B}2/s1600/IMG_1234.jpeg", f"{B}3/s1600/IMG_9999.jpg"]
    assert imgs[1].alt == "Škoda Octavia RS beyaz" and imgs[0].file == "golf-8-kirmizi.jpg"
    assert large(f"{B}1/w640-h360-rw/x.jpg", "s800") == f"{B}1/s800/x.jpg"


def test_matching_uses_alt_file_and_confident_labels_only():
    imgs = parse_archive(ARCHIVE)
    assert best_match(imgs, ["Golf"]).file == "golf-8-kirmizi.jpg"
    assert best_match(imgs, ["Octavia"], ["Skoda"]).alt.startswith("Škoda")      # Š folded
    assert best_match(imgs, ["Passat"]) is None                                    # no random photo
    imgs[2].label = {"arac": True, "marka": "Volkswagen", "model": "Passat", "guven": 90}
    assert best_match(imgs, ["Passat"]).file == "IMG_9999.jpg"
    imgs[2].label["guven"] = 40
    assert best_match(imgs, ["Passat"]) is None
    assert best_match(imgs, ["Golf"], exclude=[imgs[0].url]) is None


def test_merge_keeps_paid_labels_and_drops_removed_photos(tmp_path):
    old = parse_archive(ARCHIVE)
    old[2].label = {"arac": True, "marka": "VW", "model": "Passat", "guven": 90}
    path = str(tmp_path / "images.json")
    save(path, old)
    new = merge(load(path), parse_archive(ARCHIVE.replace("IMG_1234", "IMG_5555")))
    assert [i.label.get("model") for i in new] == [None, None, "Passat"]
    assert all("IMG_1234" not in i.url for i in new)


class FakeAI:
    def __init__(self, fail_after=99):
        self.calls, self.fail_after = [], fail_after

    def structured(self, tier, prompt, schema, system=None, step="", images=None):
        from ai_client import AIError, AIErrorKind
        if len(self.calls) >= self.fail_after:
            raise AIError(AIErrorKind.RATE_LIMITED, "quota")
        self.calls.append((tier, step, images[0][1]))
        return schema(arac=True, marka="Volkswagen", model="Golf", guven=88, gorulen="kırmızı hatchback")


def fetch_ok(url):
    return FetchResult(url, FetchOutcome.OK, 200, b"\xff\xd8\xff" + b"0" * 10, 1)


def test_labels_once_within_run_cap_and_stop_on_quota():
    imgs = parse_archive(ARCHIVE)
    ai = FakeAI()
    assert label_new(imgs, ai, fetch_ok, ImageSettings(max_labels_per_run=2)) == 2
    assert ai.calls[0] == ("fast", "image_label", "image/jpeg") and imgs[2].label == {}
    assert label_new(imgs, ai, fetch_ok) == 1 and len(ai.calls) == 3          # labeled photos are skipped
    fresh = parse_archive(ARCHIVE)
    assert label_new(fresh, FakeAI(fail_after=1), fetch_ok) == 1              # quota: the rest waits


def test_failed_download_leaves_photo_unlabeled():
    imgs = parse_archive(ARCHIVE)
    bad = lambda url: FetchResult(url, FetchOutcome.ROBOTS_DISALLOWED, detail="robots")
    assert label_new(imgs, FakeAI(), bad) == 0 and all(not i.label for i in imgs)


def test_refresh_reads_the_archive_draft(tmp_path):
    class Client:
        def find_draft(self, title):
            assert title == "Görsel arşivi"
            return {"id": "9", "content": ARCHIVE}
    imgs = refresh(Client(), str(tmp_path / "i.json"))
    assert len(imgs) == 3 and len(load(str(tmp_path / "i.json"))) == 3

    class Empty:
        def find_draft(self, title):
            return None
    import pytest
    with pytest.raises(SystemExit, match="bulunamadı"):
        refresh(Empty(), str(tmp_path / "j.json"))
