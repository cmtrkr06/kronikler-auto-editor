import json

import schema_markup as sm
from config import SchemaSettings

S = SchemaSettings()
URL = "https://otomobilkronikleri.blogspot.com/2026/10/x.html"


def post(content="<p>Metin</p>", updated="2026-10-15T10:00:00+03:00", **kw):
    p = {"id": "1", "title": "Başlık", "url": URL, "content": content, "status": "LIVE",
         "published": "2026-10-14T09:00:00+03:00", "updated": updated,
         "author": {"displayName": "Cem", "url": "https://www.blogger.com/profile/123"}}
    p.update(kw)
    return p


def blocks(html):
    return [d for _, d in sm.ld_blocks(html)]


class Client:
    def __init__(self, posts):
        self.posts = {p["id"]: p for p in posts}
        self.updates = []

    def live_posts(self):
        return list(self.posts.values())

    def update_live_content(self, pid, content):
        self.updates.append(pid)
        self.posts[pid]["content"] = content
        self.posts[pid]["updated"] = "2026-10-15T12:00:00+03:00"     # Blogger moves `updated` on patch
        return {"updated": self.posts[pid]["updated"]}


# ---------------------------------------------------------------- BlogPosting
def test_article_has_google_recommended_fields():
    p = post('<p>a</p><img src="https://cmtrkr06.github.io/kronikler-assets/a.jpg"><img src="http://x/b.jpg">')
    d = sm.article_jsonld(p, p["updated"])
    assert d["@type"] == "BlogPosting" and d["headline"] == "Başlık"
    assert d["datePublished"] == p["published"] and d["dateModified"] == p["updated"]
    assert d["author"] == [{"@type": "Person", "name": "Cem", "url": "https://www.blogger.com/profile/123"}]
    assert d["image"] == ["https://cmtrkr06.github.io/kronikler-assets/a.jpg"]       # http image skipped
    assert d["mainEntityOfPage"]["@id"] == URL


def test_author_falls_back_to_site_without_profile():
    assert sm.author_of(post(author={}))["@type"] == "Organization"


def test_script_cannot_be_closed_by_text():
    out = sm.script({"headline": "a</script><b>"})
    assert out.count("</script>") == 1 and json.loads(sm.ld_blocks(out)[0][0][36:-9])


def test_apply_writes_once_and_keeps_body():
    c = Client([post()])
    rec = {}
    assert sm.apply(c, rec)["written"] == 1
    content = c.posts["1"]["content"]
    assert content.startswith("<p>Metin</p>")
    assert [d["@type"] for d in blocks(content)] == ["BlogPosting"]
    # our own patch moved `updated`: next run must not rewrite (no dateModified loop)
    assert sm.apply(c, rec) == {"written": 0, "skipped": 1, "same": 0, "failed": 0}
    assert len(c.updates) == 1


def test_editor_edit_refreshes_date_modified_and_replaces_block():
    c = Client([post()])
    rec = {}
    sm.apply(c, rec)
    c.posts["1"]["updated"] = "2026-10-20T08:00:00+03:00"                  # edited in Blogger
    assert sm.apply(c, rec)["written"] == 1
    found = blocks(c.posts["1"]["content"])
    assert len(found) == 1 and found[0]["dateModified"] == "2026-10-20T08:00:00+03:00"


def test_dry_run_changes_nothing():
    c = Client([post()])
    assert sm.apply(c, {}, dry_run=True)["written"] == 1 and c.updates == []


def test_failed_post_does_not_stop_others():
    class Bad(Client):
        def update_live_content(self, pid, content):
            if pid == "1":
                raise RuntimeError("boom")
            return super().update_live_content(pid, content)
    c = Bad([post(), post(id="2")])
    assert sm.apply(c, {}) == {"written": 1, "skipped": 0, "same": 0, "failed": 1}


def test_no_star_markup_is_ever_written():
    c = Client([post()])
    sm.apply(c, {})
    assert not sm.RATING_MARKUP_RE.search(c.posts["1"]["content"])
