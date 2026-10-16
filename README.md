# Kronikler Auto Editor

CarScore verisinden (shiftandtrend.com) Otomobil Kronikleri bloguna arıza kroniği ve karşılaştırma
yazıları hazırlayıp Blogger'a **taslak** olarak gönderen hat. Plan ve kararlar: `docs/PLAN.md`.

```
CarScore sayfaları → ayrıştır + tutarlılık kapıları → konu planla (kod) → veri paketi [K1]..
  → yaz (Gemini) → SEO/HTML kontrolü (kod) → fact-check (ikinci model) → Blogger TASLAK
```

## Kurulum
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                       # ağa bağımlı değil, tüm HTTP sahte
python main.py --plan        # CarScore'u oku, aday konuları yaz (AI yok)
python main.py --check-ai    # GEMINI_API_KEY gerekir
python write.py --dry-run    # en iyi adayı yaz, data/drafts/ önizleme (Blogger'a gitmez)
python write.py --daily      # zamanlanmış: yaz, kontrol et, Blogger'a TASLAK
```

## Dosyalar
| Dosya | Amaç |
|---|---|
| `config.py` | Ayarlar, model adları, kotalar (önek `KRONIKLER_`) |
| `models.py` | `ChronicleTopic`, `TopicType`, `TopicStatus`, `FetchResult` |
| `fetcher.py` | Nazik HTTP: sabit UA, robots.txt, alan adı başına bekleme, 403/challenge → atla |
| `fetch_fixtures.py` | Örnek CarScore sayfalarını kaydeder (Actions: "CarScore ornek sayfalar") |
| `carscore_source.py` | CarScore sayfalarını ayrıştırır, tutarlılığı denetler, anlık görüntü + gerileme kontrolü |
| `planner.py` | Aday konular (arıza kroniği, karşılaştırma), örneklem eşikleri, tekrar/güncelleme kararı — AI yok |
| `data_pack.py` | Yazının tek dayanağı: [K1], [K2].. kimlikli veri parçaları (CarScore'dan kurulur) |
| `writer.py`, `chronicle_pipeline.py`, `write.py` | Yazım (Gemini) → SEO + sayı kontrolü → fact-check → önizleme |
| `ai_client.py`, `quota.py` | Tek AI kapısı (Gemini), model zinciri, günlük kota defteri |
| `fact_checker.py`, `seo_check.py` | Bağımsız doğruluk kontrolü; kodla HTML/SEO/kopya kontrolü |
| `image_library.py`, `visuals.py` | 'Görsel arşivi' taslağındaki resimler + kodla kapak ve veri görselleri (HTML) |
| `blogger.py`, `blogger_reauth.py`, `blogger_login.sh` | Yalnız TASLAK gönderim; telefondan giriş yenileme |
| `schema_markup.py` | Yayındaki yazılara BlogPosting JSON-LD |
| `memory.py`, `normalizer.py` | Konu/kota hafızası (JSON, Actions'ta `state` dalı); Türkçe uyumlu anahtarlar |

Kaynak: cmtrkr06/zapyus-auto-editor @ e193136 modüllerinden kopyalandı ve sadeleştirildi.
