from blogger import BloggerError, send_draft
from config import AssetSettings
from memory import InMemoryStore
from models import ChronicleTopic, TopicStatus, TopicType

PAGES = AssetSettings().pages_base
GOOD_HTML = (f'<figure><img src="{PAGES}/images/a.jpg" alt="CarPlay ekranı"></figure>'
             "<h2>Kurulum</h2><p>Metin.</p>")


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else {"id": "123", "status": "DRAFT"}
        self.error = error
        self.calls = []

    def insert_draft(self, title, content, labels):
        self.calls.append((title, content, labels))
        if self.error:
            raise self.error
        return self.response


def saved(**kw):
    d = {"ready": True, "html": GOOD_HTML, "labels": ["Apple CarPlay"],
         "draft": {"title": "CarPlay nasıl bağlanır", "meta_description": "m"}}
    d.update(kw)
    return d


def topic(status=TopicStatus.PROPOSED):
    return ChronicleTopic("carplay", "DQ200 kroniği", TopicType.FAULT, status)


def test_sends_ready_draft_and_marks_topic():
    store, client, t = InMemoryStore(), FakeClient(), topic()
    r = send_draft(t, saved(), client, store)
    assert r.ok and r.post_id == "123"
    assert client.calls == [("CarPlay nasıl bağlanır", GOOD_HTML, ["Apple CarPlay"])]
    assert store.get_topic("carplay").status == TopicStatus.DRAFTED
    assert any(l["module"] == "blogger" and l["details"]["post_id"] == "123" for l in store.logs)


def test_refuses_not_ready_already_sent_local_images_and_foreign_images():
    cases = [
        (topic(), saved(ready=False), "HAZIR değil"),
        (topic(TopicStatus.DRAFTED), saved(), "zaten"),
        (topic(), saved(blogger={"post_id": "9"}), "zaten"),
        (topic(), saved(html='<img src="../assets/images/a.jpg" alt="x">'), "yerel görsel"),
        (topic(), saved(html='<img src="https://baska.com/a.jpg" alt="x">'), "görsel kontrolü"),
        (topic(), saved(html=f'<img src="{PAGES}/images/a.jpg" alt="">'), "alt metin"),
    ]
    for t, s, msg in cases:
        client = FakeClient()
        r = send_draft(t, s, client, InMemoryStore())
        assert not r.ok and msg in r.message and client.calls == [], msg


def test_api_failure_is_logged_and_topic_stays_approved():
    store, t = InMemoryStore(), topic()
    r = send_draft(t, saved(), FakeClient(error=BloggerError("posts.insert başarısız: HttpError 500")), store)
    assert not r.ok and t.status == TopicStatus.PROPOSED
    assert any(l["level"] == "ERROR" and l["module"] == "blogger" for l in store.logs)


def test_missing_post_id_is_failure():
    store, t = InMemoryStore(), topic()
    r = send_draft(t, saved(), FakeClient(response={"status": "DRAFT"}), store)
    assert not r.ok and t.status == TopicStatus.PROPOSED


def test_real_client_always_sends_is_draft_true():
    from blogger import GoogleBloggerClient
    seen = {}

    class Posts:
        def insert(self, **kw):
            seen.update(kw)
            return type("Req", (), {"execute": lambda self: {"id": "1"}})()

    client = GoogleBloggerClient.__new__(GoogleBloggerClient)   # skip OAuth in the test
    client.blog_id = "b1"
    client.service = type("S", (), {"posts": lambda self: Posts()})()
    assert client.insert_draft("T", "<p>x</p>", ["L"]) == {"id": "1"}
    assert seen["isDraft"] is True and seen["blogId"] == "b1"
    assert seen["body"] == {"kind": "blogger#post", "title": "T", "content": "<p>x</p>", "labels": ["L"]}


# ---- updating an existing draft
from blogger import update_draft


class DraftClient(FakeClient):
    def __init__(self, statuses=("DRAFT", "DRAFT"), **kw):
        super().__init__(**kw)
        self.statuses = list(statuses)
        self.patches = []
        self.updated = "2026-10-03T12:33:40+00:00"   # Blogger's timestamp right after our send

    def get_post(self, post_id):
        return {"id": post_id, "status": self.statuses.pop(0), "updated": self.updated}

    def patch_draft(self, post_id, title, content, labels):
        self.patches.append((post_id, title, content, labels))
        return {"id": post_id}


SENT = {"post_id": "123", "sent_at": "2026-10-03T12:33:36+00:00"}


def test_update_patches_draft_and_verifies_it_stays_draft():
    store, client = InMemoryStore(), DraftClient()
    r = update_draft(topic(TopicStatus.DRAFTED), saved(blogger=SENT), client, store)
    assert r.ok and client.patches == [("123", "CarPlay nasıl bağlanır", GOOD_HTML, ["Apple CarPlay"])]


def test_update_never_touches_a_published_post():
    client = DraftClient(statuses=("LIVE",))
    r = update_draft(topic(TopicStatus.DRAFTED), saved(blogger={"post_id": "123"}), client, InMemoryStore())
    assert not r.ok and "taslak değil" in r.message and client.patches == []


def test_update_flags_status_change_after_patch():
    store = InMemoryStore()
    r = update_draft(topic(TopicStatus.DRAFTED), saved(blogger=SENT), DraftClient(("DRAFT", "LIVE")), store)
    assert not r.ok and any(l["level"] == "CRITICAL" for l in store.logs)


def test_update_requires_existing_post_and_public_images():
    assert not update_draft(topic(), saved(), DraftClient(), InMemoryStore()).ok
    bad = saved(blogger={"post_id": "1"}, html='<img src="../assets/x.jpg" alt="a">')
    assert "yerel" in update_draft(topic(), bad, DraftClient(), InMemoryStore()).message


def test_real_client_patch_sends_publish_false():
    from blogger import GoogleBloggerClient
    seen = {}

    class Posts:
        def patch(self, **kw):
            seen.update(kw)
            return type("Req", (), {"execute": lambda self: {"id": "1"}})()

    client = GoogleBloggerClient.__new__(GoogleBloggerClient)
    client.blog_id = "b1"
    client.service = type("S", (), {"posts": lambda self: Posts()})()
    client.patch_draft("1", "T", "<p>x</p>", ["L"])
    assert seen["publish"] is False and seen["postId"] == "1"
    assert seen["body"] == {"title": "T", "content": "<p>x</p>", "labels": ["L"]}


def test_update_refuses_a_draft_edited_in_blogger_unless_overwrite():
    client = DraftClient(statuses=("DRAFT", "DRAFT", "DRAFT", "DRAFT"))
    client.updated = "2026-10-03T15:00:00+00:00"           # editor worked on it later
    r = update_draft(topic(TopicStatus.DRAFTED), saved(blogger=SENT), client, InMemoryStore())
    assert not r.ok and "düzenlenmiş" in r.message and client.patches == []
    r = update_draft(topic(TopicStatus.DRAFTED), saved(blogger=SENT), client, InMemoryStore(), overwrite_edits=True)
    assert r.ok and len(client.patches) == 1


def test_unknown_timestamps_count_as_edited():
    from blogger import edited_in_blogger
    assert edited_in_blogger({}, SENT) and edited_in_blogger({"updated": "x"}, SENT)
    assert edited_in_blogger({"updated": "2026-10-03T12:33:40+00:00"}, {})
    assert not edited_in_blogger({"updated": "2026-10-03T12:34:00+00:00"}, SENT)


# ---- 2026-10-04: drafts saved by write.py carry "blogger": null until sent
def test_fresh_draft_with_null_blogger_field_is_sent():
    store, client = InMemoryStore(), FakeClient()
    r = send_draft(topic(), saved(blogger=None), client, store)
    assert r.ok and len(client.calls) == 1


def test_update_with_null_blogger_field_is_refused_cleanly():
    r = update_draft(topic(), saved(blogger=None), DraftClient(), InMemoryStore())
    assert not r.ok and "önce gönderilmeli" in r.message


# ---- the blog decides what is a duplicate (2026-10-06: CarPlay guide sent twice from a stale state)
class ListingClient(FakeClient):
    def __init__(self, titles=(), listing_error=None, **kw):
        super().__init__(**kw)
        self.titles, self.listing_error = list(titles), listing_error

    def existing_titles(self):
        if self.listing_error:
            raise self.listing_error
        return self.titles


def test_title_already_on_the_blog_is_not_sent_again():
    store, client = InMemoryStore(), ListingClient(titles=["CarPlay Nasıl Bağlanır"])
    r = send_draft(topic(), saved(), client, store)
    assert not r.ok and "aynı başlıklı" in r.message and client.calls == []
    assert store.get_topic("carplay") is None or store.get_topic("carplay").status != TopicStatus.DRAFTED


def test_new_title_is_sent_and_failed_listing_blocks_the_send():
    store, client = InMemoryStore(), ListingClient(titles=["Başka bir yazı"])
    assert send_draft(topic(), saved(), client, store).ok
    blocked = ListingClient(listing_error=BloggerError("posts.list başarısız: 500"))
    r = send_draft(topic(), saved(), blocked, InMemoryStore())
    assert not r.ok and "kopya kontrolü yapılamadı" in r.message and blocked.calls == []
