# Kronikler Auto Editor — proje talimatları

Otomobil Kronikleri (https://otomobilkronikleri.blogspot.com/) için, yalnız CarScore verisinden
(https://shiftandtrend.com/araclar/) yazı üreten ve Blogger'a TASLAK gönderen otomatik hat.
Plan: `docs/PLAN.md` (önce onu oku). Zapyus hattının (cmtrkr06/zapyus-auto-editor, e193136) kopyası
üzerine kurulu; iki depo bağımsız, birindeki değişiklik diğerine otomatik geçmez.

## Kararlar (2026-10-16, kullanıcı)
- Ayrı depo. Veri: CarScore'un herkese açık sayfaları okunur (eklentiye dokunulmaz).
- Haftada 3 yazı, günde en çok 1. Önce TASLAK; yayın elle.
- Blogger: Zapyus ile aynı Google hesabı (aynı OAuth istemcisi, farklı blog ID).
- İlk sürümde yazı türleri: arıza kroniği + karşılaştırma. Araç sahip kroniği ve ay kroniği sonra.
- Yıldız/aggregateRating YOK; yalnız BlogPosting (puanlar shiftandtrend'de toplanıyor).

## Durum
- Adım 0 TAMAM (2026-10-16): Zapyus'tan ai_client, quota, memory (sade), models (ChronicleTopic),
  normalizer, fetcher (crawler'ın Fetcher'ı), fact_checker, seo_check, blogger, blogger_reauth,
  schema_markup (yalnız BlogPosting), data_pack (ResearchPack şekli). 161 test.
- Adım 1 TAMAM (2026-10-16): Actions'tan erişim var (robots izinli, 200). `carscore_source.py` (BeautifulSoup,
  açık seçiciler, ParseError; tutarlılık: seçenek toplamı = bildirim, arızalı = sorun seçenekleri toplamı,
  tek seçimli anket ~%100, ana sayfa kartı = ayrıntı sayfası, araç sayfasındaki birim bölümü = birim sayfası;
  anlık görüntü data/carscore/<gün>.json, bir önceki güne göre gerileme → o konu bekler), `data_pack.py`
  kurucuları, `planner.py` (AI yok), `python main.py --plan`, Actions "Konu plani (AI yok)". 189 test.
- Site gerçekleri (2026-10-16): motor sayfasında arıza bölümü YOK (araç sayfasından okunur); araç sayfasındaki
  arıza bölümü birimi kullanan TÜM araçların toplamı; anket yüzdelerinin soru başına tabanı sayfada yok.
  Sitenin kendi eşikleri: anket sorusu 3 katılımcıdan azsa yüzde yerine KİŞİ SAYISI gösterilir ("Henüz az veri"
  notu) → `Outcome.unit="adet"`, `Vehicle.low_data`; arıza bölümü 10-12 bildirime ulaşmadan dağılım yok
  ("N bildirim toplandı") → `Vehicle.pending_faults`. "N kişinin görüşü" hızlı oyları da sayar; ayrıntılı anket
  yanıtı çok daha az olabilir (CUPRA Leon: 10 görüş, ~2 yanıt) → karşılaştırma `survey_ready` ister.
- Sıradaki: Adım 2 (yazar/SEO/fact-check uyarlaması + yerel önizleme). Kullanıcı onayı gerekir.

## Kurallar
- Kullanıcıyla Türkçe konuş. Yazılar Türkçe.
- URL, model adı, API parametresi UYDURMA; resmi dokümandan doğrula.
- Çalıştırmadığın testi "geçti" diye raporlama.
- Etik okuma: sabit UA, robots.txt, nazik bekleme, 401/403/challenge → atla, bypass yok.
- Yazıdaki HER sayı veri paketinden gelir; fact-check aynı pakete bakar. Pakette yoksa yazılmaz.
- Ayrıştırıcı bir zorunlu alanı bulamazsa o sayfa HATA, konu yazılmaz. Tahmin/varsayılan sayı yok.
- Örneklem eşiği altındaki konu yazılmaz. Arıza verisi adet olarak yazılır, oran olarak değil; anket yüzdesi
  kişi sayısına çevrilmez (docs/PLAN.md bölüm 4).
- Fiyat yazılmaz. Tamir masrafı yalnız sahip bildirimi aralığı olarak.
- Secret'lar sadece .env / GitHub Secrets'ta. Blogger'a yalnız TASLAK (isDraft=true).
- HTML gövdede H1 yok (H2-H4). Model adları, kotalar, eşikler config'de.
- Her büyük adımdan önce kullanıcı onayı al.

## Komutlar
- Kurulum: `python -m venv .venv` → aktive et → `pip install -r requirements.txt`
- Test: `pytest`
- AI doğrulama: `python main.py --check-ai` (1 API çağrısı) · Kota: `python main.py --quota`
- Örnek sayfalar (yerelde): `python fetch_fixtures.py fixtures/raw`
- Konu planı (AI yok): `python main.py --plan [--snapshot dosya] [--show ariza-dq200]`
