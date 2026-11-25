import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import psycopg

# Google AI Studio & Groq Developer quotas reset on Pacific Time / rolling 24h
def _quota_day_str() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def ensure_usage_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_model_usage (
                usage_date DATE NOT NULL,
                model_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                calls_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                remaining_requests INTEGER,
                limit_requests INTEGER,
                remaining_tokens INTEGER,
                limit_tokens INTEGER,
                quota_exhausted BOOLEAN NOT NULL DEFAULT FALSE,
                last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (usage_date, model_id)
            );
        """)
    conn.commit()


def record_ai_usage(
    database_url: str,
    model_id: str,
    provider: str,
    *,
    calls_delta: int = 1,
    tokens_delta: int = 0,
    headers: dict | None = None,
    quota_exhausted: bool = False,
):
    """Upserts per-model daily usage and parses Groq x-ratelimit-remaining-* headers when present."""
    if not database_url:
        return

    rem_req = lim_req = rem_tok = lim_tok = None
    if headers:
        lower_h = {k.lower(): v for k, v in headers.items()}
        try:
            if "x-ratelimit-remaining-requests" in lower_h:
                rem_req = int(lower_h["x-ratelimit-remaining-requests"])
            if "x-ratelimit-limit-requests" in lower_h:
                lim_req = int(lower_h["x-ratelimit-limit-requests"])
            if "x-ratelimit-remaining-tokens" in lower_h:
                rem_tok = int(lower_h["x-ratelimit-remaining-tokens"])
            if "x-ratelimit-limit-tokens" in lower_h:
                lim_tok = int(lower_h["x-ratelimit-limit-tokens"])
        except (TypeError, ValueError):
            pass

    day_str = _quota_day_str()
    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            ensure_usage_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_model_usage (
                        usage_date, model_id, provider, calls_used, tokens_used,
                        remaining_requests, limit_requests, remaining_tokens, limit_tokens,
                        quota_exhausted, last_used_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (usage_date, model_id) DO UPDATE SET
                        calls_used = ai_model_usage.calls_used + EXCLUDED.calls_used,
                        tokens_used = ai_model_usage.tokens_used + EXCLUDED.tokens_used,
                        remaining_requests = COALESCE(EXCLUDED.remaining_requests, ai_model_usage.remaining_requests),
                        limit_requests = COALESCE(EXCLUDED.limit_requests, ai_model_usage.limit_requests),
                        remaining_tokens = COALESCE(EXCLUDED.remaining_tokens, ai_model_usage.remaining_tokens),
                        limit_tokens = COALESCE(EXCLUDED.limit_tokens, ai_model_usage.limit_tokens),
                        quota_exhausted = EXCLUDED.quota_exhausted OR ai_model_usage.quota_exhausted,
                        last_used_at = EXCLUDED.last_used_at;
                    """,
                    (
                        day_str,
                        model_id,
                        provider,
                        calls_delta,
                        tokens_delta,
                        rem_req,
                        lim_req,
                        rem_tok,
                        lim_tok,
                        quota_exhausted,
                        datetime.now(timezone.utc),
                    ),
                )
    except Exception:
        pass
