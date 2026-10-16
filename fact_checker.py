"""[F] FACT CHECK - a second model reads the finished article against the same CarScore data pack.

The checker extracts the claims from the ARTICLE itself (it does not trust the writer's claim
list) and reports every one that the pack does not support or contradicts. The verdict is
code, not the model's: a contradiction or a critical unsupported claim is a critical error;
with `fail_on_unsupported` (brief md. 18) any unsupported claim fails the article.
Output keeps the brief's shape: passed, critical_errors, warnings, unsupported_claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from typing import Literal, Optional

from pydantic import BaseModel, Field

from ai_client import AIClient
from config import FactCheckSettings
from data_pack import ResearchPack


class Finding(BaseModel):
    alinti: str = Field(description="Yazıdaki ilgili ifade, kısa ve aynen")
    durum: Literal["desteklenmiyor", "celisiyor"] = Field(
        description="desteklenmiyor: kaynakta yok; celisiyor: kaynakla çelişiyor")
    onem: Literal["kritik", "uyari"] = Field(
        description="kritik: sayı, yüzde, adet, km, tutar, puan, model/birim adı, teknik değer ya da "
                    "verinin anlamını değiştiren (oran/hüküm) ifade; uyari: küçük süsleme veya genelleme")
    aciklama: str = Field(description="Neden; kaynakta ne yazdığı")
    parcalar: list[str] = Field(description="İlgili parça kimlikleri (varsa), örn. [\"K3\"]")


class FactCheckOutput(BaseModel):
    findings: list[Finding] = Field(description="Desteklenmeyen veya çelişen her iddia; hiçbiri yoksa boş")
    ozet: str = Field(description="Tek cümlelik genel değerlendirme")


SYSTEM = """Titiz bir doğruluk denetçisisin. Sana bir Türkçe otomobil yazısı ve yazının dayanması gereken VERİ
PAKETİ verilecek. Paket, CarScore'daki (shiftandtrend.com) sahip anketi ve arıza bildirimlerinden kurulmuştur. Görevin:
1. Yazıdaki her bilgi içeren ifadeyi (her sayı, yüzde, adet, km, tutar, yıl, puan, model ve birim adı, teknik değer)
   veri paketiyle karşılaştır.
2. Paketle desteklenmeyen ya da paketle çelişen HER ifadeyi bulgu olarak yaz. Kendi genel bilgin doğru olduğunu
   söylese bile pakette yoksa "desteklenmiyor" sayılır (geri çağırma, servis bülteni, fiyat, model yılı dahil).
3. Şunlar her zaman bulgudur:
   - Pakette olmayan bir oran ya da yüzde hesaplamak (ör. 84 bildirimde 11 → "%13 arıza") → desteklenmiyor.
   - Bildirim sayılarını "arıza oranı" ya da kesin hüküm gibi sunmak (paket bunların gönüllü bildirim olduğunu,
     temsili olmadığını söyler) → celisiyor.
   - Anket yüzdesini kişi sayısına çevirmek → desteklenmiyor.
   - Bir motor/şanzımanı kullanan TÜM araçların bildirimini tek bir modele mal etmek → celisiyor.
   - Az bildirime dayanan bir bilgiyi TABANINI SÖYLEMEDEN ya da genelleyen bir fiille ("yoğunlaşıyor", "genellikle",
     "çoğunlukla", "sahipler ... ödüyor") sunmak → celisiyor. Tabanı yanında açıkça yazılmışsa ("4 bildirimin ortancası
     50 – 75 Bin TL", "4 bildirimin en sık cevabı ...") bu DOĞRU aktarımdır, bulgu YAZMA.
   - Sayılar arasında kurulan her ilişkiyi ("geri kalan 67", "toplam", "çoğunluk", "en büyük grup") paketteki
     sayılarla HESAPLAYARAK kontrol et; tutmuyorsa → celisiyor (ör. 84 bildirimin 11'i arızalıysa geri kalan 73'tür).
   - Paketin belirsiz bıraktığı bir şeyi kesinleştirmek (ör. bir sayının hangi gruba ait olduğu) → desteklenmiyor.
   - Bir seçeneğin anlamını değiştirmek (ör. "henüz onarılmadı" → "servise girmedi") → celisiyor.
4. Doğru ve pakette karşılığı olan ifadeleri yazma. Üslup, akış ve dilbilgisiyle ilgilenme. Ondalıkta virgül ya da
   nokta farkı (5,5 / 5.5) bulgu değildir.
Uydurma bulgu yazma; emin değilsen yazma."""


def readable_text(html: str) -> str:
    """HTML -> plain text with headings and list items on their own lines."""
    t = re.sub(r"(?i)<(h[2-4])>", "\n## ", html)
    t = re.sub(r"(?i)<li>", "\n- ", t)
    t = re.sub(r"(?i)</(p|h[2-4]|li|tr)>|<br>", "\n", t)
    t = unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{2,}", "\n", t).strip()


def build_prompt(title: str, body_html: str, pack: ResearchPack) -> str:
    return (f"YAZI BAŞLIĞI: {title}\n\nYAZI:\n{readable_text(body_html)}\n\n"
            f"VERİ PAKETİ:\n{pack.render()}")


@dataclass
class FactCheckResult:
    passed: bool
    critical_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    summary: str = ""
    model: str = ""

    def to_dict(self) -> dict:
        return {"passed": self.passed, "critical_errors": self.critical_errors, "warnings": self.warnings,
                "unsupported_claims": self.unsupported_claims, "summary": self.summary, "model": self.model}

    def feedback(self) -> list[str]:
        """What the writer must fix in a rewrite."""
        return ([f"DOĞRULUK HATASI: {e}" for e in self.critical_errors]
                + [f"KAYNAKTA YOK, çıkar veya kaynağa uygun yaz: {u}" for u in self.unsupported_claims
                   if not any(u in e for e in self.critical_errors)])


def verdict(output: FactCheckOutput, settings: FactCheckSettings, model: str) -> FactCheckResult:
    critical, warnings, unsupported = [], [], []
    for f in output.findings:
        line = f"'{f.alinti.strip()}' - {f.aciklama.strip()}"
        if f.durum == "desteklenmiyor":
            unsupported.append(f.alinti.strip())
        if f.durum == "celisiyor" or f.onem == "kritik":
            critical.append(line)
        else:
            warnings.append(line)
    passed = not critical and not (settings.fail_on_unsupported and unsupported)
    return FactCheckResult(passed, critical, warnings, unsupported, output.ozet.strip(), model)


class FactChecker:
    def __init__(self, ai: AIClient, settings: FactCheckSettings = FactCheckSettings()) -> None:
        self.ai = ai
        self.settings = settings

    def check(self, title: str, body_html: str, pack: ResearchPack,
              writer_model: Optional[str] = None) -> FactCheckResult:
        """Raises AIError if the model cannot be reached. `writer_model` never checks its own text."""
        output = self.ai.structured(self.settings.tier, build_prompt(title, body_html, pack), FactCheckOutput,
                                    system=SYSTEM, step="fact_check", fallback=True,
                                    avoid_model=writer_model or None)
        return verdict(output, self.settings, self.ai.last_model)
