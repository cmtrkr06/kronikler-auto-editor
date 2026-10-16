"""Write -> SEO -> fact-check -> (one rewrite with the findings) for one article.

Copied from zapyus-auto-editor (e193136) guide_pipeline.py. No I/O besides the AI calls, so the whole
decision flow is testable with fakes. An article is READY only if SEO (structure + every number in the
pack) passed and the fact check passed; otherwise nothing may be sent to Blogger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ai_client import AIError
from config import FactCheckSettings
from fact_checker import FactChecker, FactCheckResult
from data_pack import ResearchPack
from models import ChronicleTopic
from writer import WriteResult, Writer


@dataclass
class ChronicleOutcome:
    result: WriteResult
    fact_check: Optional[FactCheckResult]       # None when SEO failed or the check is pending
    rounds: list[dict] = field(default_factory=list)  # every fact-check round, for the record
    check_error: str = ""                       # checker unreachable: the draft is kept, check later

    @property
    def pending_check(self) -> bool:
        return bool(self.check_error)

    @property
    def ready(self) -> bool:
        return self.result.seo.passed and self.fact_check is not None and self.fact_check.passed

    @property
    def reason(self) -> str:
        if not self.result.seo.passed:
            return "SEO: " + "; ".join(self.result.seo.errors)
        if self.pending_check:
            return "fact-check bekliyor: " + self.check_error
        if self.fact_check is None or not self.fact_check.passed:
            items = (self.fact_check.critical_errors or self.fact_check.unsupported_claims) if self.fact_check else []
            return "fact-check: " + "; ".join(items[:5])
        return ""


class ChroniclePipeline:
    def __init__(self, writer: Writer, checker: FactChecker,
                 settings: FactCheckSettings = FactCheckSettings()) -> None:
        self.writer = writer
        self.checker = checker
        self.settings = settings

    def run(self, topic: ChronicleTopic, pack: ResearchPack, existing: Optional[WriteResult] = None) -> ChronicleOutcome:
        """`existing`: a saved draft whose check is still pending - it is checked, not rewritten.
        Raises AIError when the WRITER is unreachable; a checker failure keeps the draft."""
        result = existing if existing is not None else self.writer.write(topic, pack)
        rounds: list[dict] = []
        while True:
            if not result.seo.passed:
                return ChronicleOutcome(result, None, rounds)
            try:
                check = self.checker.check(result.draft.title, result.body, pack, result.model)
            except AIError as exc:
                return ChronicleOutcome(result, None, rounds, check_error=str(exc))
            rounds.append(check.to_dict())
            if check.passed or len(rounds) > self.settings.max_revisions:
                return ChronicleOutcome(result, check, rounds)
            result = self.writer.write(topic, pack, feedback=check.feedback())
