# Kronikler Auto Editor — plan

Durum: ONAYLANDI (2026-10-16). Adım 0 ve 1 tamam; Adım 2 kodu tamam, ilk önizleme Gemini anahtarını bekliyor.
Hedef: https://otomobilkronikleri.blogspot.com/ için, yalnız CarScore verisinden (shiftandtrend.com/araclar/)
yazı üreten ve Blogger'a TASLAK gönderen otomatik hat. Zapyus hattının (cmtrkr06/zapyus-auto-editor,
commit e193136) kanıtlanmış parçaları kopyalanır; ayrı depo, ayrı state dalı, ayrı Gemini anahtarı.

## 1. Zapyus'tan farkı tek cümlede
Zapyus haber sitelerini tarar ve kaynak sayfalardan paket kurar. Kronikler hiçbir dış sayfayı taramaz:
"kaynak paketi" CarScore'un kendi verisidir. Yazının her sayısı bu paketten gelir, fact-check aynı pakete bakar.

## 2. Kopyalanacaklar / yazılacaklar
| Zapyus'tan kopya (testleriyle) | Değişiklik |
|---|---|
| `ai_client.py`, `quota.py`, `memory.py` | yok (yalnız önek `KRONIKLER_`) |
| `crawler.py` (yalnız Fetcher), `normalizer.py` | UA: KroniklerAutoEditor |
| `fact_checker.py`, `seo_check.py` | rehber zorunlu yapısı yerine Kronikler yapısı |
| `blogger.py`, `blogger_reauth.py` | yok; yeni blog ID ve token |
| `schema_markup.py` | yalnız BlogPosting; site adı/adresi |
| `image_sources.py`, `image_manager.py`, `asset_host.py` | Wikimedia Commons model fotoğrafı; ayrı açık görsel deposu |
| `gunluk.sh`, iş akışları (günlük, Blogger girişi yenile, konu kararı) | yeni adlar |

| Yeni | Görevi |
|---|---|
| `carscore_source.py` | CarScore sayfalarını okur (Fetcher), ayrıştırır, tutarlılığı doğrular, `data/carscore/<gün>.json` anlık görüntü |
| `data_pack.py` | Bir konunun verisini [K1], [K2]... kimlikli parçalara çevirir (ResearchPack ile aynı şekil) |
| `planner.py` | Konu seçimi tamamen KODDA, AI yok: aday konular, örneklem eşikleri, dönüşüm, tekrar ve güncelleme kararı |
| `writer.py` (uyarlama) | Kronikler sesi: anlatı tonu, her sayıda "N sahipten M'si" biçimi |
| `overlap.py` | shiftandtrend.com'daki yazı başlıklarına bakar; aynı araç + aynı açı yakın zamanda yazıldıysa konu atlanır |

Atılanlar: `news.py`, `relevance.py`, `clustering.py`, `scoring.py`, `topic_map.py`,
`researcher.py`, `strengthen.py` (ilk sürümde). Embedding ve haber kotası harcanmaz.

## 3. Veri erişimi — KARAR (2026-10-16): sayfaları okumak (eklentiye dokunulmaz)
CarScore'un herkese açık sayfaları okunur: `/araclar/` (kronik sorun listesi), `/sanziman/<kod>/`,
`/motor/<kod>/`, `/arac/<slug>/`. Zapyus'un `crawler.py` Fetcher'ı kullanılır: sabit UA, robots.txt, nazik
bekleme, 403/challenge → atla. JSON uç noktası seçeneği (eklentide Faz) ileride gerekirse açılır.

Kırılganlığa karşı kurallar (en büyük risk SESSİZ yanlış sayı):
- Ayrıştırıcı her alanı açık seçiciyle okur; zorunlu alan bulunamazsa o sayfa HATA, konu yazılmaz, log'a
  "ayrıştırıcı güncellenmeli" yazılır. Tahmin/varsayılan değer yok.
- Tutarlılık kapıları: arızalı ≤ toplam bildirim, oranlar 0-100, dağılımlar ~%100, anket sayısı ≥ alt toplamlar.
- Gerçek sayfalardan kaydedilmiş HTML örnekleri `tests/fixtures/` altında; tüm ayrıştırıcı testleri bunlarla.
- Her çalıştırma `data/carscore/<gün>.json` anlık görüntüsü yazar; bir önceki güne göre aşırı sapma
  (örn. bildirim sayısı düştü) → uyarı, o konu o gün yazılmaz.
- GitHub Actions sunucularından siteye erişim (güvenlik duvarı engeli olabilir) Adım 1'in ilk kontrolü.

## 4. Yazı türleri ve kapılar (kodda) — ilk sürüm: arıza kroniği + karşılaştırma (karar 2026-10-16)
Araç sahip kroniği ve ay kroniği sonraki sürüme kaldı.
| Tür | Örnek başlık | En az veri |
|---|---|---|
| Arıza kroniği | DQ200 Kroniği: 84 Sahipten 11'i Arıza Bildirdi | 30 bildirim |
| Karşılaştırma | Formentor mu Leon mu? Sahip Verisiyle | iki araç da 10 anket |

- Örneklem eşik altıysa konu yazılmaz. Arıza verisi ADET olarak yazılır ("84 bildirimden 11'i"), oran olarak
  değil: CarScore'un kendi notu bildirimlerin gönüllü ve temsili olmadığını söylüyor (sayfadaki "arıza oranı
  değildir" notu pakete girer). Anket yüzdeleri için sayfa soru başına yanıt sayısı vermiyor: yüzde, toplam
  katılımcı sayısıyla birlikte yazılır ("72 katılımcılı ankette %58"), kişi sayısına çevrilmez.
- Araç sayfasındaki arıza bölümü o aracın değil, motor/şanzımanı kullanan TÜM araçların toplamıdır;
  karşılaştırmada bir modele mal edilmez.
- "Kesin arızalı", "asla alınmaz" gibi hükümler yasak; dil: "sahiplerin bildirdiği".
- Fiyat yazılmaz (CarScore kararı: fiyat ön yüzde yok). Tamir masrafı yalnız sahip bildirimi aralığı olarak.
- Her yazının sonunda kodla eklenen "Veri kaynağı" kutusu: CarScore sayfasına link, veri tarihi, örneklem.
- Aynı konu ikinci kez yazılmaz; veri belirgin değişirse (örn. anket +%30) mevcut yazının GÜNCELLEME taslağı.
- shiftandtrend.com'da aynı araç + aynı açıda son 90 günde yazı varsa konu atlanır (iki site yarışmasın).

## 5. Ritim ve kota
- Haftada 3 yazı (karar 2026-10-16), günde en çok 1 (Google'ın toplu üretim içerik politikasına karşı düşük hız). Önce TASLAK,
  yayın elle.
- Gemini: ayrı proje açılamadı (2026-10-16); Zapyus projesinin ikinci anahtarı, kota paylaşımlı. Kronikler'in
  günlük tavanı küçük, çalıştırma Zapyus'tan sonra. Yazı başına 1-2 yazar + 1-2 fact-check isteği.
- Yazmaya değer veri yoksa 0 yazı geçerli sonuçtur.

## 6. Yapılandırılmış veri
Yalnız BlogPosting. aggregateRating/yıldız YOK: puanlar bu blogda değil CarScore'da toplanıyor; başka sitenin
oyunu bu sayfada yıldız olarak işaretlemek Google kurallarına aykırı. (Yıldız CarScore sayfalarında kalır.)

## 7. Uygulama sırası (her adım ayrı rapor + onay)
0. Depo iskeleti, kopyalanan modüller ve testleri yeşil.
1. Actions'tan erişim kontrolü + gerçek sayfa örnekleri (fixture); `carscore_source.py` + `data_pack.py`;
   `python main.py --plan` aday konuları basar (AI yok).
2. `planner.py` + `overlap.py` + yazar/SEO/fact-check uyarlaması → `write.py --dry-run` yerel önizleme.
3. Blogger taslağı, görseller, BlogPosting şeması, iş akışları ve zamanlama.

## 8. Kullanıcıdan gerekenler
- GitHub'da boş, özel `cmtrkr06/kronikler-auto-editor` deposu (Claude depo açamıyor).
- Secret'lar: `GEMINI_API_KEY` (yeni proje), `BLOGGER_ID` (Kronikler), `BLOGGER_TOKEN_JSON`, `ASSETS_TOKEN`.
- Blogger: aynı Google hesabı (karar 2026-10-16) → Zapyus'un OAuth istemcisi kullanılır, yalnız blog ID farklı.
