"""[W] WRITER - one Kronikler article from one CarScore data pack (DEEP model, structured output).

Adapted from zapyus-auto-editor (e193136) writer.py. The model returns the article fields plus
`claims`: every factual statement with the pack passages ([K1]..) it rests on. Code then sanitizes the
HTML, adds the "Veri kaynağı" box (CarScore pages + data date; the model never writes URLs), picks
labels and runs seo_check, which also rejects any number that is not in the pack. On blocking errors
the model gets one rewrite with the errors fed back; the fact-checker works on the result.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from html import escape
from typing import Optional

from pydantic import BaseModel, Field

from ai_client import AIClient
from config import WriterSettings
from data_pack import NAME_LIST, ResearchPack
from models import ChronicleTopic, TopicType
from normalizer import topic_key
from seo_check import SeoReport, check, sanitize_html, title_problems

log = logging.getLogger("kronikler.writer")


class Claim(BaseModel):
    iddia: str = Field(description="Yazıdaki bir bilgi ya da sayı, kısa")
    kaynaklar: list[str] = Field(description="Bu bilgiyi içeren parça kimlikleri, örn. [\"K2\"]")


class Draft(BaseModel):
    title: str = Field(description="Yazının başlığı (Blogger bunu H1 yapar), en fazla 70 karakter; birim ya da "
                                   "araç adını ve paketteki gerçek bir sayıyı içerir")
    title_alternatives: list[str] = Field(
        default_factory=list,
        description="İki farklı tarzda alternatif başlık (biri soru, biri çarpıcı bir sayı), aynı kurallarla")
    seo_title: str = Field(description="Arama sonuçları için başlık, en fazla 60 karakter")
    meta_description: str = Field(description="Arama açıklaması, 70-150 karakter, net ve tıklama odaklı")
    slug: str = Field(description="Küçük harf, Türkçe karaktersiz, tireli URL parçası")
    tags: list[str] = Field(description="En fazla 3 etiket (marka, model, birim kodu)")
    html_body: str = Field(description="Yazı gövdesi; yalnız p, h2, h3, h4, ul, ol, li, strong, em, table, thead, "
                                       "tbody, tr, th, td, blockquote etiketleri; H1 ve link YOK")
    image_query: str = Field(description="Kapak görseli için spesifik İngilizce arama sorgusu (araç/model adı)")
    claims: list[Claim] = Field(description="Gövdedeki (tablo ve Veri notu dahil) her bilgi/sayı ve dayandığı parçalar")
    internal_link_ideas: list[str] = Field(
        default_factory=list,
        description="Blogda bu yazıyla bağlanabilecek 1-3 ilgili konu başlığı (gövdeye YAZILMAZ; editöre öneri)")


COMMON = """Otomobil Kronikleri (otomobilkronikleri.blogspot.com) adlı Türkçe otomobil blogunun yazarısın. Blog,
VAG grubu araçların (Volkswagen, Škoda, SEAT, CUPRA, Audi) sahiplerinden toplanan verinin hikâyesini anlatır.
Tek kaynağın: CarScore (shiftandtrend.com) sahip anketi ve arıza bildirimleri. Bu veri sana VERİ PAKETİ olarak
[K1], [K2]... parçalarıyla verilecek.

DOĞRULUK (en önemli kural)
1. Yalnız VERİ PAKETİNDEKİ bilgileri ve SAYILARI kullan. Yazıdaki her sayı (başlık dahil) pakette aynen geçmeli.
   Oran HESAPLAMA (84'te 11 → "%13" YASAK), yuvarlama yapma, yüzdeyi kişi sayısına ÇEVİRME (yüzdelerin soru
   başına tabanı yok), paket dışı fiyat, model yılı, teknik değer, servis bülteni, geri çağırma YAZMA.
   Pakette olmayan bir şeyi tahminle doldurma; bilmiyorsan yazma.
   Sayılar arasında ilişki kurarken ("geri kalan", "toplam", "çoğunluk", "en büyük grup") hesabı paketteki
   sayılarla tek tek doğrula; seçenek adlarını paketteki gibi kullan, başka anlama kaydırma ("henüz onarılmadı"
   ≠ "servise girmedi"). Paket bir şeyin belirsiz olduğunu söylüyorsa sen de belirsiz bırak.
2. Arıza verisi gönüllü sahip BİLDİRİMİDİR, temsili değildir: "84 bildirimden 11'inde arıza bildirildi" diye ADET
   olarak yaz; "arıza oranı", "arızalı çıkıyor", "kesin arızalı", "uzak durun", "asla alınmaz" gibi hüküm YOK.
   Az bildirime dayanan bir bilgiyi (ör. "4 bildirimin ortancası") HER ZAMAN tabanıyla birlikte ver ve genelleyen fiil
   kullanma: "arızalar 80-100 bin km'de yoğunlaşıyor" YANLIŞ; "kilometre sorusunu yanıtlayan 8 bildirimin ortancası
   80 – 100 Bin" DOĞRU. "Ortanca" bir yoğunlaşma, çoğunluk ya da eğilim değildir.
3. Blog aracı kendisi test etmedi: "test ettik, sürdük, denedik, kendi aracımızda" gibi birinci el deneyim
   YAZMA. Deneyim sahiplerindir: "sahiplerin bildirdiğine göre", "ankete katılanların %58'i" gibi.
4. Paketteki cümleleri kopyalama; kendi cümlelerinle, okura dönük ve sade anlat.

YAPI (html_body)
5. Gövde başlıksız bir GİRİŞ paragrafıyla başlar: ilk 2-3 cümlede konuyu ve verinin en önemli bulgusunu
   paketteki sayılarla söyle. Ansiklopedik ya da dolgu giriş YASAK.
6. Sahip verisini en az bir TABLOYLA ver (<table><thead><tr><th>..</th></tr></thead><tbody>..</tbody></table>,
   en az iki veri satırı). Tablodaki her hücre paketten gelir.
7. Yazıda tam bir VERİ NOTU kutusu olsun: <blockquote><p><strong>Veri notu:</strong> ...</p></blockquote>.
   İçinde sayıların ne olduğunu ve ne olmadığını pakete dayanarak açıkla (gönüllü bildirim, temsili değil;
   yüzdeler ankete katılanların payı).
8. "Sonuç", "Değerlendirme", "Özet" başlığı AÇMA. Yazı verinin söylediği son somut noktayla bitsin.
9. HTML gövdede H1 yok; alt başlıklar H2/H3. Gövdeye link ve görsel koyma (veri kaynağı kutusunu ve görselleri
   sistem ekler).

BAŞLIK (çok önemli: okur tıklasın ama yanılmasın)
- title merak uyandırsın: sahiplerin sesini, şaşırtıcı bir sayıyı ya da okurun aklındaki soruyu öne çıkar
  (ör. "DQ200 Sahipleri Anlattı: 84 Bildirimin 11'inde DSG Arızası", "DQ200'de Arıza Kaçıncı Kilometrede?").
- title_alternatives: iki farklı tarzda alternatif (biri soru başlığı, biri çarpıcı bir sayıyla).
- Her başlık en fazla 70 karakter; içindeki her sayı pakette aynen geçer; veri desteklemeyen vaat ya da hüküm
  YOK ("alınmaz", "kesin arızalı", "herkes pişman" gibi). Büyük harfle bağırma, ünlem yağmuru, "şok" yok.

DİL
10. Bilgili bir otomobil yazarı gibi sade, akıcı Türkçe; robotik ve kurumsal dil yok. Ondalıkta virgül (5,5).
    Şunlar YASAK: sonuç olarak, özetlemek gerekirse, unutmayın ki, kusursuz, devrim niteliğinde, büyüleyici,
    çığır açıyor, ezber bozuyor, günümüz dünyasında.
11. Uzunluğu paket belirler: dolgu ve tekrar yapma.
12. claims listesine gövdedeki her bilgiyi/sayıyı ve hangi parça(lar)dan geldiğini yaz."""

FAULT = """
BU YAZI: ARIZA KRONİĞİ. Tek bir şanzıman ya da motorun sahip bildirimlerini anlat.
- Başlık birim kodunu (ör. DQ200) ve paketteki gerçek bir sayıyı içersin; clickbait yok.
- Tablo: bildirim seçenekleri ve adetleri. Varsa kaç km'de olduğu, masrafı kimin ödediği, tutar, uygulanan
  çözüm ve bakım (yağ/filtre) bilgisi ayrı bölümlerde, her biri dayandığı bildirim sayısıyla.
- Paketteki araç listesinden bu birimi kullanan modelleri say; paketteki teknik künyeyi kısa ver."""

COMPARE = """
BU YAZI: KARŞILAŞTIRMA. İki aracı yalnız sahip verisiyle karşılaştır.
- Başlık iki aracı adlandırsın; "hangisi daha iyi" diye hüküm VERME, verinin nerede ayrıştığını göster.
- Tablo: iki aracı yan yana (CarScore, görüş sayısı, kullanıcı puanı, tekrar alırdım yüzdesi, gerçek tüketim,
  en çok övülen ve şikâyet edilen yön...).
- Arıza bölümleri o aracın değil, motoru/şanzımanı kullanan TÜM araçların toplamıdır: öyle yaz, tek modele mal etme.
- Bir araçta sahip verisi az ise (pakette "az veri" geçiyorsa) bunu açıkça belirt."""

SYSTEMS = {TopicType.FAULT: COMMON + FAULT, TopicType.COMPARE: COMMON + COMPARE}


def length_target(pack_chars: int, settings: WriterSettings) -> tuple[int, int]:
    for min_chars, target in settings.length_targets:
        if pack_chars >= min_chars:
            return target
    return settings.length_targets[-1][1]


def build_prompt(topic: ChronicleTopic, pack: ResearchPack, feedback: Optional[list[str]] = None,
                 settings: WriterSettings = WriterSettings()) -> str:
    low, high = length_target(pack.total_chars, settings)
    parts = [f"KONU: {topic.title} (yazının başlığını kendin kur, en fazla 70 karakter)",
             f"HEDEF UZUNLUK: {low}-{high} kelime. Paket yetmiyorsa daha kısa yaz; dolgu, tekrar ve paket dışı bilgi asla.",
             f"VERİ TARİHİ: {pack.created_at[:10] or '-'}",
             f"VERİ PAKETİ ({pack.total_chars} karakter):", pack.render()]
    if feedback:
        parts += ["", "ÖNCEKİ TASLAK ŞU HATALARLA REDDEDİLDİ, bunları düzelterek baştan yaz:"]
        parts += [f"- {e}" for e in feedback]
    return "\n".join(parts)


@dataclass
class WriteResult:
    topic_key: str
    draft: Draft
    html: str                           # sanitized body + code-built data source box
    body: str                           # sanitized body only (what the fact-checker reads)
    slug: str
    labels: list[str]
    source_urls: list[str]
    seo: SeoReport
    revisions: int = 0
    history: list[list[str]] = field(default_factory=list)  # SEO errors of rejected attempts
    model: str = ""                     # the model that wrote it; the fact-check must use another
    title_options: list[str] = field(default_factory=list)   # the other headlines that passed, for the editor


def source_box(pack: ResearchPack) -> tuple[str, list[str]]:
    """Every CarScore page of the pack (not only cited ones: the reader should see the whole source)."""
    seen: dict[str, str] = {}
    for p in pack.passages:
        if p.url not in seen:
            seen[p.url] = p.title.split(":", 1)[-1].strip() or p.url
    if not seen:
        return "", []
    day = pack.created_at[:10]
    when = f"{day[8:10]}.{day[5:7]}.{day[:4]}" if len(day) == 10 else ""
    items = "".join(f'<li><a href="{escape(u, quote=True)}">{escape(t)}</a></li>' for u, t in seen.items())
    return ("<h2>Veri kaynağı</h2><p>Bu yazıdaki tüm sayılar CarScore (shiftandtrend.com) sahip anketi ve arıza "
            f"bildirimlerinden{' ' + when + ' tarihinde' if when else ''} alınmıştır. Sayılar yeni bildirimlerle "
            f"değişebilir; güncel hâli için:</p><ul>{items}</ul>"), list(seen)


def choose_title(draft: Draft, sources: list[str]) -> tuple[Draft, list[str]]:
    """The model's main headline if it passes, else the first alternative that does (code decides)."""
    options = [t.strip() for t in [draft.title, *draft.title_alternatives] if t and t.strip()]
    good = [t for t in dict.fromkeys(options) if not title_problems(t, sources)]
    if good and good[0] != draft.title.strip():
        draft = draft.model_copy(update={"title": good[0]})
    return draft, good[1:]


def evaluate(topic: ChronicleTopic, pack: ResearchPack, draft: Draft, settings: WriterSettings) -> WriteResult:
    draft, alternatives = choose_title(draft, [p.text for p in pack.passages])
    body, removed = sanitize_html(draft.html_body)
    slug = topic_key(draft.slug) or topic_key(draft.title)
    claim_ids = [[k.strip().strip("[]").upper() for k in c.kaynaklar] for c in draft.claims]
    known = {p.id for p in pack.passages}
    report = check(html=body, seo_title=draft.seo_title.strip(), meta_description=draft.meta_description.strip(),
                   slug=slug, allowed_urls=[], source_texts=[p.text for p in pack.passages],
                   claim_ids=claim_ids, known_ids=known, removed_tags=removed,
                   kind=topic.kind, title=draft.title.strip(),
                   copy_texts=[p.text for p in pack.passages if not p.title.endswith(NAME_LIST)])
    floor = dict(settings.min_words).get(topic.type.value, 0)
    if report.word_count < floor:
        report.errors.append(f"yazı çok kısa: {report.word_count} kelime (en az {floor}); paketteki verinin "
                             "ne anlattığını eksiksiz açıkla (dolgu değil, veri)")
    box, urls = source_box(pack)
    labels = [dict(settings.labels).get(topic.type.value, topic.type.value)]
    for t in draft.tags:
        t = t.strip()
        if t and t not in labels and len(labels) <= settings.max_tags:
            labels.append(t)
    return WriteResult(topic.key, draft, body + box, body, slug, labels, urls, report, title_options=alternatives)


class Writer:
    def __init__(self, ai: AIClient, settings: WriterSettings = WriterSettings()) -> None:
        self.ai = ai
        self.settings = settings

    def write(self, topic: ChronicleTopic, pack: ResearchPack, feedback: Optional[list[str]] = None) -> WriteResult:
        """Raises AIError if the model cannot be reached; SEO failures come back in result.seo.
        `feedback`: problems found in an earlier version (e.g. by the fact-checker) to fix."""
        history: list[list[str]] = []
        while True:
            draft = self.ai.structured(self.settings.tier, build_prompt(topic, pack, feedback, self.settings), Draft,
                                       system=SYSTEMS[topic.type], step="writer", fallback=True)
            result = evaluate(topic, pack, draft, self.settings)
            result.model = self.ai.last_model
            result.revisions = len(history)
            result.history = list(history)
            if result.seo.passed or len(history) >= self.settings.max_seo_revisions:
                return result
            log.warning("draft for %s rejected by SEO check, rewriting: %s", topic.key, result.seo.errors)
            history.append(result.seo.errors)
            feedback = result.seo.errors
