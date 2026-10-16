import json

import pytest

from ai_client import AIClient
from config import AISettings, FactCheckSettings
from data_pack import Passage, ResearchPack
from fact_checker import (FactChecker, FactCheckOutput, Finding, build_prompt, readable_text, verdict)
from fakes import FakeBackend

# The writer/fact-check pipeline tests come back with the Kronikler writer (plan step 2).
PACK = ResearchPack("ariza-dq200", "DQ200 kroniği", [
    Passage("K1", "carscore:sanziman/dq200", "https://shiftandtrend.com/sanziman/dq200/", "CarScore DQ200",
            "84 bildirimin 11'inde arıza. Genelde 80-100 bin km (8 bildirim).")])


def finding(text="Type-C kablo", durum="desteklenmiyor", onem="uyari"):
    return Finding(alinti=text, durum=durum, onem=onem, aciklama="pakette yalnız Lightning", parcalar=["K2"])


def out(*findings, ozet="özet"):
    return FactCheckOutput(findings=list(findings), ozet=ozet)


# ---- verdict (pure)
def test_clean_article_passes():
    r = verdict(out(), FactCheckSettings(), "m")
    assert r.passed and r.to_dict() == {"passed": True, "critical_errors": [], "warnings": [],
                                        "unsupported_claims": [], "summary": "özet", "model": "m"}


def test_minor_unsupported_fails_under_brief_rule_but_not_when_relaxed():
    strict = verdict(out(finding()), FactCheckSettings(), "m")
    assert not strict.passed and strict.unsupported_claims == ["Type-C kablo"] and strict.critical_errors == []
    relaxed = verdict(out(finding()), FactCheckSettings(fail_on_unsupported=False), "m")
    assert relaxed.passed and relaxed.warnings


@pytest.mark.parametrize("durum,onem", [("celisiyor", "uyari"), ("desteklenmiyor", "kritik")])
def test_contradiction_or_critical_is_critical_even_when_relaxed(durum, onem):
    r = verdict(out(finding(durum=durum, onem=onem)), FactCheckSettings(fail_on_unsupported=False), "m")
    assert not r.passed and r.critical_errors


def test_feedback_lists_each_problem_once():
    r = verdict(out(finding("Ayarlar > Gizli menü", onem="kritik"), finding("toz")), FactCheckSettings(), "m")
    fb = r.feedback()
    assert sum("Ayarlar > Gizli menü" in x for x in fb) == 1
    assert any(x.startswith("DOĞRULUK HATASI") for x in fb) and any("toz" in x for x in fb)


# ---- text / prompt
def test_readable_text_and_prompt():
    t = readable_text("<h2>Kurulum</h2><p>İlk &amp; ikinci</p><ul><li>bir</li><li>iki</li></ul>")
    assert t == "## Kurulum\nİlk & ikinci\n- bir\n- iki"
    p = build_prompt("Başlık", "<p>x</p>", PACK)
    assert "YAZI BAŞLIĞI: Başlık" in p and "[K1] CarScore DQ200" in p


def test_checker_uses_check_model_and_step():
    backend = FakeBackend(generate=[out().model_dump_json()])
    ai = AIClient(AISettings(), backend, sleep=lambda s: None)
    r = FactChecker(ai).check("t", "<p>x</p>", PACK)
    assert backend.prompts[0][0] == AISettings().check_model == r.model
