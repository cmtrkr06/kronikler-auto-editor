"""Central configuration. Model names, quotas, thresholds live here, never in the code.

Copied from zapyus-auto-editor (e193136) and trimmed to what Kronikler uses. Env prefix: KRONIKLER_.
The Gemini key is a SEPARATE Google project (docs/PLAN.md section 5), so Zapyus' free-tier day is
never spent here. Model names and limits below are Zapyus' verified values (2026-10-03/07); the new
project's own list and limits are re-checked with `python main.py --check-ai` once the key exists.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

BLOG_URL = "https://otomobilkronikleri.blogspot.com/"
DEFAULT_USER_AGENT = f"KroniklerAutoEditor/1.0 (+{BLOG_URL})"


@dataclass(frozen=True)
class Settings:
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 15.0
    max_retries: int = 3                 # retries AFTER the first attempt
    max_408_retries: int = 2
    backoff_base: float = 2.0            # seconds; doubles each retry
    backoff_cap: float = 60.0
    retry_after_cap: float = 120.0       # never sleep longer than this for a 429
    per_domain_delay: float = 2.0        # polite gap between requests to one domain
    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    memory_backend: str = "json"         # "json" | "memory"
    memory_path: str = "data/memory.json"
    log_level: str = "INFO"


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Build Settings from environment variables (see .env.example)."""
    if env is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:  # python-dotenv is optional at runtime
            pass
        env = os.environ
    d = Settings()
    return Settings(
        user_agent=d.user_agent,  # intentionally not overridable: honest, fixed UA
        request_timeout=float(env.get("KRONIKLER_REQUEST_TIMEOUT", d.request_timeout)),
        max_retries=int(env.get("KRONIKLER_MAX_RETRIES", d.max_retries)),
        per_domain_delay=float(env.get("KRONIKLER_DOMAIN_DELAY", d.per_domain_delay)),
        memory_backend=env.get("KRONIKLER_MEMORY_BACKEND", d.memory_backend),
        memory_path=env.get("KRONIKLER_MEMORY_PATH", d.memory_path),
        log_level=env.get("KRONIKLER_LOG_LEVEL", d.log_level).upper(),
    )


# APIs that publish their own automation policy: only that path skips robots.txt (zapyus decision
# 2026-10-03). Commons is used for cover photos in step 3.
API_POLICY_EXCEPTIONS: tuple = (
    ("https://commons.wikimedia.org/w/api.php",
     "Wikimedia Foundation User-Agent Policy: scripts allowed with an informative User-Agent "
     "and contact info; requests in series (mediawiki.org API:Etiquette)"),
)


@dataclass(frozen=True)
class AISettings:
    """Same models and chain as Zapyus (verified there 2026-10-02/07 with models.list())."""
    fast_model: str = "gemini-3.5-flash-lite"
    deep_model: str = "gemini-3.8-flash"        # writer
    check_model: str = "gemini-3.7-flash"       # fact-check: a different model, its own daily quota
    # Busy (503) or out of quota -> the next model of the tier's chain takes over. The fact-check
    # still runs on a model other than the writer's (structured(avoid_model=...)).
    fallback_models: tuple = (
        ("deep", ("gemini-3.7-flash", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3-flash-preview")),
        ("check", ("gemini-3.8-flash", "gemini-3-flash-preview", "gemini-3.5-flash", "gemini-3.6-flash")),
    )
    embed_model: str = "gemini-embedding-2"     # not used by Kronikler; ai_client still verifies it
    embed_dimensions: int = 768
    embed_batch_size: int = 50
    relevance_batch_size: int = 40
    call_budget: int = 20                       # max API calls per run, retries included
    request_timeout: float = 180.0              # a full article in structured JSON can take > 1 min
    sdk_attempts: int = 1                       # no hidden SDK retries (they count against the quota)
    max_server_retries: int = 0                 # busy -> next model at once (zapyus 2026-10-07)
    server_retry_delays: tuple = (20.0, 60.0)
    max_invalid_output_retries: int = 2
    max_rate_limit_waits: int = 2
    rate_limit_default_wait: float = 30.0
    rate_limit_wait_cap: float = 90.0


def load_ai_settings(env: Optional[Mapping[str, str]] = None) -> AISettings:
    env = os.environ if env is None else env
    d = AISettings()
    return AISettings(
        fast_model=env.get("KRONIKLER_FAST_MODEL", d.fast_model),
        deep_model=env.get("KRONIKLER_DEEP_MODEL", d.deep_model),
        check_model=env.get("KRONIKLER_CHECK_MODEL", d.check_model),
        embed_model=env.get("KRONIKLER_EMBED_MODEL", d.embed_model),
        embed_dimensions=int(env.get("KRONIKLER_EMBED_DIMENSIONS", d.embed_dimensions)),
        call_budget=int(env.get("KRONIKLER_AI_CALL_BUDGET", d.call_budget)),
        max_server_retries=int(env.get("KRONIKLER_SERVER_RETRIES", d.max_server_retries)),
    )


@dataclass(frozen=True)
class QuotaSettings:
    """Daily request budgets per Pacific-time day (the API's reset). Our caps stay below the
    provider's free-tier limits measured on Zapyus' project (AI Studio, 2026-10-03): 3.8-flash 20,
    3.7-flash 20, 3.5-flash-lite 500, embedding-2 1000 (per input text). No planner step uses AI,
    so there are no step shares to protect the writer."""
    daily_limits: dict = field(default_factory=lambda: {
        "gemini-3.8-flash": 18,
        "gemini-3.7-flash": 18,
        "gemini-3.5-flash-lite": 450,
        "gemini-embedding-2": 900,
    })
    step_limits: dict = field(default_factory=dict)
    history_days: int = 7


@dataclass(frozen=True)
class FactCheckSettings:
    tier: str = "check"                 # a different model from the writer, its own daily quota
    fail_on_unsupported: bool = True    # a claim (a number!) not in the data pack -> FAIL
    max_revisions: int = 1              # one rewrite with the findings fed back


@dataclass(frozen=True)
class SchemaSettings:
    """BlogPosting JSON-LD on every LIVE post (schema_markup.py). No stars (plan section 6)."""
    site_name: str = "Otomobil Kronikleri"
    site_url: str = BLOG_URL
    author_name: str = ""               # "" -> Blogger author.displayName
    author_url: str = ""                # "" -> Blogger author.url (profile page)
    max_images: int = 3
    auto_apply: bool = True
    record_path: str = "data/schema.json"


@dataclass(frozen=True)
class CarScoreSettings:
    """Reading CarScore's public pages (carscore_source.py). Markup checked 2026-10-16 on the pages
    saved in tests/fixtures/ (fetched from GitHub Actions: robots.txt allows them, 200 everywhere)."""
    base_url: str = "https://shiftandtrend.com"
    hub_path: str = "/araclar/"
    snapshot_dir: str = "data/carscore"
    # Fault outcome bars that are NOT a fault. Every other bar counts as one. The site's own headline
    # count ("84 bildirimden 11 tanesinde arıza") must equal the sum of the others (consistency gate).
    no_issue_labels: tuple = ("Hayır, sorun yaşamadım",)
    unknown_labels: tuple = ("Bilmiyorum / aracı yeni aldım",)
    # Survey questions with ONE answer per owner: their percentages must add up to ~100.
    single_choice_questions: tuple = ("Bu aracı tekrar alır mıydın?", "Aracı hangi amaçla kullanıyorsun?",
                                      "Şu an kaç kilometrede?")
    rounding_slack: int = 2             # percent points lost/gained by rounding
    # Vehicle pages read per run (hub class leaders + one per engine fault). Polite and bounded.
    max_vehicle_pages: int = 15
    # Planner gates (docs/PLAN.md section 4)
    min_fault_reports: int = 30         # a fault chronicle needs at least this many owner reports
    min_compare_opinions: int = 10      # both vehicles of a comparison need this many owner opinions
    # A drop between two snapshots means the source changed under us: that subject waits a day.
    max_report_drop: int = 0


@dataclass(frozen=True)
class AssetSettings:
    """Images on GitHub Pages of a dedicated public repo, as in Zapyus. The repo is created in step 3."""
    owner: str = "cmtrkr06"
    repo: str = "kronikler-assets"
    branch: str = "main"
    pages_base: str = "https://cmtrkr06.github.io/kronikler-assets"
    local_dir: str = "data/assets"      # dry-run previews use local copies
