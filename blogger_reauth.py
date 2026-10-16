"""Blogger girişini bilgisayarsız yenilemek (telefondan, GitHub Actions ile).

`python blogger.py --auth` yerel bir sunucu açar; bu yüzden bilgisayar ister. Burada aynı
izin akışı iki adıma bölünür (Google "installed app" akışı, loopback yönlendirme):

  python blogger_reauth.py url                 -> izin bağlantısını yazar
  python blogger_reauth.py exchange <kod|URL>  -> kodu token.json'a çevirir

İstemci kimliği ve sırrı mevcut token.json'dan okunur (GitHub'da BLOGGER_TOKEN_JSON secret'ı).
İzin verildikten sonra tarayıcı http://127.0.0.1:8080/?code=... adresine gider; sayfa açılmaz,
ama adres çubuğundaki adres (ya da yalnız code değeri) exchange'e verilir.

Uç noktalar: https://developers.google.com/identity/protocols/oauth2/native-app
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "http://127.0.0.1:8080/"
SCOPE = "https://www.googleapis.com/auth/blogger"


def load_client(token_path: str = "token.json") -> dict:
    with open(token_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("client_id", "client_secret"):
        if not data.get(key):
            raise SystemExit(f"{token_path} içinde {key} yok")
    return data


def auth_url(client_id: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",   # refresh_token için
        "prompt": "consent",        # her seferinde yeni refresh_token versin
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def extract_code(value: str) -> str:
    """Accepts the bare code or the whole redirect URL copied from the address bar."""
    value = value.strip()
    if "code=" in value:
        query = urllib.parse.urlparse(value).query or value.split("?", 1)[-1]
        codes = urllib.parse.parse_qs(query).get("code")
        if codes:
            return codes[0]
    if value.startswith("http"):
        raise SystemExit("Adreste code= yok; izin reddedilmiş olabilir")
    return urllib.parse.unquote(value)


def build_token(client: dict, response: dict) -> dict:
    if not response.get("refresh_token"):
        raise SystemExit("Google refresh_token vermedi; bağlantıyı yeniden açıp izin verin")
    return {
        "token": response.get("access_token"),
        "refresh_token": response["refresh_token"],
        "token_uri": client.get("token_uri") or DEFAULT_TOKEN_URI,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "scopes": [SCOPE],
        "universe_domain": client.get("universe_domain", "googleapis.com"),
        "account": client.get("account", ""),
    }


def exchange(client: dict, code: str) -> dict:
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(client.get("token_uri") or DEFAULT_TOKEN_URI, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"Kod çevrilemedi (HTTP {exc.code}): {detail}. Kod tek kullanımlık ve "
                         "kısa ömürlü; bağlantıyı yeniden açıp yeni kodla deneyin.") from exc


def main(argv: list[str]) -> int:
    if argv[:1] == ["url"]:
        print(auth_url(load_client()["client_id"]))
        return 0
    if len(argv) == 2 and argv[0] == "exchange":
        client = load_client()
        token = build_token(client, exchange(client, extract_code(argv[1])))
        with open("token.json", "w", encoding="utf-8") as fh:
            json.dump(token, fh)
        print("token.json yenilendi.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
