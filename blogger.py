"""[B] BLOGGER - sends a READY article to Blogger as a DRAFT. Never publishes a new post.

Copied from zapyus-auto-editor (e193136); news/feature branches removed (Kronikler has one topic
model, models.ChronicleTopic). The one write to a LIVE post is update_live_content(), used only by
schema_markup.py for our JSON-LD block; it changes the content alone, never the title, labels, URL
or publish date.

API facts (developers.google.com/blogger/docs/3.0, checked 2026-10-03): posts.insert takes
`isDraft`; writable fields used here are title, content (HTML) and labels. There is no field
for the search description or a custom permalink: the description is reported for the editor
to paste, the URL is Blogger's own until the permalink test (stage 3, step 6) has been done.

Guards, all in send_draft(): the draft must be READY (SEO + fact check passed), the topic must
not have been sent before, every image must already be on the public asset host, and the
request always carries isDraft=True.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol

from config import ImageSettings
from memory import MemoryStore
from models import ChronicleTopic, TopicStatus, utcnow
from normalizer import topic_key
from seo_check import check_images

log = logging.getLogger("kronikler.blogger")
SCOPES = ["https://www.googleapis.com/auth/blogger"]


class BloggerError(Exception):
    pass


class BloggerClient(Protocol):
    def insert_draft(self, title: str, content: str, labels: list[str]) -> dict: ...
    def find_draft(self, title: str) -> Optional[dict]: ...
    def get_post(self, post_id: str) -> dict: ...
    def patch_draft(self, post_id: str, title: str, content: str, labels: list[str]) -> dict: ...
    def live_posts(self) -> list[dict]: ...
    def update_live_content(self, post_id: str, content: str) -> dict: ...


class GoogleBloggerClient:
    """google-api-python-client. token.json is created once by `python blogger.py --auth`
    (browser consent by the blog owner) and refreshed automatically afterwards."""

    def __init__(self, blog_id: str, token_path: str = "token.json") -> None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        if not blog_id:
            raise BloggerError("BLOGGER_ID is not set (.env)")
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except (OSError, ValueError) as exc:
            raise BloggerError(f"{token_path} okunamadı; önce: python blogger.py --auth") from exc
        if not creds.valid:
            if not creds.refresh_token:
                raise BloggerError("Blogger yetkisi geçersiz; önce: python blogger.py --auth")
            try:
                creds.refresh(Request())
            except Exception as exc:  # google.auth.exceptions.RefreshError and transport errors
                raise BloggerError(f"Blogger yetkisi yenilenemedi ({type(exc).__name__}); "
                                   "python blogger.py --auth") from exc
            with open(token_path, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        self.blog_id = blog_id
        self.service = build("blogger", "v3", credentials=creds, cache_discovery=False)

    def insert_draft(self, title: str, content: str, labels: list[str]) -> dict:
        body = {"kind": "blogger#post", "title": title, "content": content, "labels": labels}
        try:
            return self.service.posts().insert(blogId=self.blog_id, body=body, isDraft=True).execute()
        except Exception as exc:  # googleapiclient.errors.HttpError and transport errors
            raise BloggerError(f"posts.insert başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def existing_titles(self) -> list[str]:
        """Titles of every post, live, draft or scheduled (one request per 500 posts)."""
        titles: list[str] = []
        token = None
        try:
            while True:
                resp = self.service.posts().list(
                    blogId=self.blog_id, status=["LIVE", "DRAFT", "SCHEDULED"], view="AUTHOR",
                    fetchBodies=False, maxResults=500, pageToken=token,
                    fields="items(title),nextPageToken").execute()
                titles += [p.get("title", "") for p in resp.get("items", [])]
                token = resp.get("nextPageToken")
                if not token:
                    return titles
        except Exception as exc:
            raise BloggerError(f"posts.list başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def find_draft(self, title: str) -> Optional[dict]:
        """The DRAFT whose title matches (normalized, e.g. the 'Görsel arşivi' image archive), with body."""
        want = topic_key(title)
        token = None
        try:
            while True:
                resp = self.service.posts().list(
                    blogId=self.blog_id, status=["DRAFT"], view="AUTHOR", fetchBodies=True, maxResults=50,
                    pageToken=token, fields="items(id,title,content,updated),nextPageToken").execute()
                for p in resp.get("items", []):
                    if topic_key(p.get("title", "")) == want:
                        return p
                token = resp.get("nextPageToken")
                if not token:
                    return None
        except Exception as exc:
            raise BloggerError(f"posts.list (taslak) başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def get_post(self, post_id: str) -> dict:
        try:
            return self.service.posts().get(blogId=self.blog_id, postId=post_id, view="AUTHOR").execute()
        except Exception as exc:
            raise BloggerError(f"posts.get başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def patch_draft(self, post_id: str, title: str, content: str, labels: list[str]) -> dict:
        # publish=False explicitly: the discovery document lists `publish` for patch without a default
        body = {"title": title, "content": content, "labels": labels}
        try:
            return self.service.posts().patch(blogId=self.blog_id, postId=post_id, body=body,
                                              publish=False).execute()
        except Exception as exc:
            raise BloggerError(f"posts.patch başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def live_posts(self) -> list[dict]:
        """Every LIVE post with its body (id, title, url, content, labels, status, published, updated,
        author, images; the last four feed schema_markup.py)."""
        posts: list[dict] = []
        token = None
        try:
            while True:
                resp = self.service.posts().list(
                    blogId=self.blog_id, status="LIVE", view="AUTHOR", fetchBodies=True, maxResults=50,
                    pageToken=token, fields="items(id,title,url,content,labels,status,published,updated,author,images),nextPageToken").execute()
                posts += resp.get("items", [])
                token = resp.get("nextPageToken")
                if not token:
                    return posts
        except Exception as exc:
            raise BloggerError(f"posts.list başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc

    def update_live_content(self, post_id: str, content: str) -> dict:
        """Content only; the post must already be LIVE (checked here, not trusted from the caller)."""
        post = self.get_post(post_id)
        if post.get("status") != "LIVE":
            raise BloggerError(f"{post_id} yayında değil (durum: {post.get('status')}); içerik güncellenmedi")
        body = {"content": content}
        if post.get("published"):
            body["published"] = post["published"]    # pinned: the update must not look like a new post
        try:
            return self.service.posts().patch(blogId=self.blog_id, postId=post_id, body=body).execute()
        except Exception as exc:
            raise BloggerError(f"posts.patch (yayındaki) başarısız: {type(exc).__name__}: {str(exc)[:200]}") from exc


@dataclass
class SendResult:
    ok: bool
    message: str
    post_id: str = ""
    post: Optional[dict] = None


def already_sent(topic: ChronicleTopic, saved: dict, store: MemoryStore) -> bool:
    if (saved.get("blogger") or {}).get("post_id"):
        return True
    return topic.status == TopicStatus.DRAFTED


def mark_sent(topic: ChronicleTopic, post_id: str, store: MemoryStore) -> None:
    topic.status = TopicStatus.DRAFTED
    topic.editor_note = (topic.editor_note + f" | Blogger taslak {post_id}").strip(" |")
    topic.updated_at = utcnow()
    store.save_topic(topic)


def duplicate_title(title: str, client: BloggerClient) -> str:
    """Why the blog already has this article, or "". Our own memory can lag behind the blog
    (2026-10-06: a stale state branch sent the CarPlay guide a second time), so the blog decides.
    A client without a listing (test fakes) skips the check; a failed listing blocks the send."""
    lister = getattr(client, "existing_titles", None)
    if lister is None:
        return ""
    try:
        taken = {topic_key(t) for t in lister()}
    except BloggerError as exc:
        return f"kopya kontrolü yapılamadı, gönderilmedi: {exc}"
    if topic_key(title) in taken:
        return "blogda aynı başlıklı yazı var (yayında ya da taslakta); kopya gönderilmedi"
    return ""


def send_draft(topic: ChronicleTopic, saved: dict, client: BloggerClient, store: MemoryStore,
               images: ImageSettings = ImageSettings()) -> SendResult:
    """`saved`: the draft JSON written by write.py, with image URLs already public."""
    if not saved.get("ready"):
        return SendResult(False, "taslak HAZIR değil (SEO veya fact-check'ten geçmemiş)")
    if already_sent(topic, saved, store):
        return SendResult(False, "bu konu zaten Blogger'a taslak olarak gönderilmiş")
    html = saved["html"]
    if "../assets/" in html:
        return SendResult(False, "yazıda yerel görsel adresi kaldı; önce görseller yayımlanmalı")
    image_errors = check_images(html, images.allowed_prefixes)
    if image_errors:
        return SendResult(False, "görsel kontrolü: " + "; ".join(image_errors))
    duplicate = duplicate_title(saved["draft"]["title"], client)
    if duplicate:
        store.add_log("blogger", "WARNING", f"{topic.key}: {duplicate}")
        return SendResult(False, duplicate)
    try:
        post = client.insert_draft(saved["draft"]["title"], html, saved["labels"])
    except BloggerError as exc:
        store.add_log("blogger", "ERROR", f"{topic.key}: {exc}")
        return SendResult(False, str(exc))
    post_id = str(post.get("id", ""))
    if not post_id:
        store.add_log("blogger", "ERROR", f"{topic.key}: yanıtta post id yok")
        return SendResult(False, "Blogger yanıtında post id yok", post=post)
    mark_sent(topic, post_id, store)
    store.add_log("blogger", "INFO", f"{topic.key}: taslak oluşturuldu", details={"post_id": post_id,
                                                                                  "status": post.get("status")})
    return SendResult(True, "taslak oluşturuldu", post_id, post)


def edited_in_blogger(post: dict, info: dict) -> bool:
    """True if the post changed after our last write (the editor worked on it in Blogger)."""
    ours = info.get("updated_at") or info.get("sent_at")
    theirs = post.get("updated")
    if not ours or not theirs:
        return True  # unknown -> assume edited, never overwrite blindly
    try:
        return datetime.fromisoformat(theirs) > datetime.fromisoformat(ours) + timedelta(minutes=2)
    except ValueError:
        return True


def update_draft(topic: ChronicleTopic, saved: dict, client: BloggerClient, store: MemoryStore,
                 images: ImageSettings = ImageSettings(), overwrite_edits: bool = False) -> SendResult:
    """Replace title, body and labels of an existing Blogger DRAFT. A post that is not a draft any
    more (published or scheduled) is never touched; a draft edited in Blogger after our last write
    is not overwritten unless `overwrite_edits`."""
    post_id = (saved.get("blogger") or {}).get("post_id", "")
    if not post_id:
        return SendResult(False, "bu konunun Blogger taslağı yok; önce gönderilmeli")
    if not saved.get("ready"):
        return SendResult(False, "taslak HAZIR değil (SEO veya fact-check'ten geçmemiş)")
    html = saved["html"]
    if "../assets/" in html:
        return SendResult(False, "yazıda yerel görsel adresi kaldı; önce görseller yayımlanmalı")
    image_errors = check_images(html, images.allowed_prefixes)
    if image_errors:
        return SendResult(False, "görsel kontrolü: " + "; ".join(image_errors))
    try:
        before = client.get_post(post_id)
        if before.get("status") != "DRAFT":
            return SendResult(False, f"Blogger'daki yazı taslak değil (durum: {before.get('status')}); "
                                     "yayımlanmış yazıya otomatik dokunulmaz")
        if not overwrite_edits and edited_in_blogger(before, (saved.get("blogger") or {})):
            return SendResult(False, f"taslak Blogger'da düzenlenmiş (son değişiklik {before.get('updated')}); "
                                     "üzerine yazmak için --overwrite-edits")
        client.patch_draft(post_id, saved["draft"]["title"], html, saved["labels"])
        after = client.get_post(post_id)
    except BloggerError as exc:
        store.add_log("blogger", "ERROR", f"{topic.key}: {exc}")
        return SendResult(False, str(exc))
    if after.get("status") != "DRAFT":
        store.add_log("blogger", "CRITICAL", f"{topic.key}: güncellemeden sonra durum {after.get('status')}",
                      details={"post_id": post_id})
        return SendResult(False, f"UYARI: güncellemeden sonra durum {after.get('status')}", post_id, after)
    store.add_log("blogger", "INFO", f"{topic.key}: taslak güncellendi", details={"post_id": post_id})
    return SendResult(True, "taslak güncellendi", post_id, after)  # caller keeps after["updated"]


def authorize(client_secret: str = "client_secret.json", token_path: str = "token.json") -> None:
    """One-time browser consent by the blog owner (not run automatically)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(token_path, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--auth"]:
        authorize()
        print("token.json yazıldı.")
    else:
        print("Kullanım: python blogger.py --auth")
