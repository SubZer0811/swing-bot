import logging
import time
from typing import Optional, Callable

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel, Field

import config

log = logging.getLogger(__name__)


class QuotaExceededError(RuntimeError):
    pass


class StockRecommendation(BaseModel):
    ticker: str
    action: str = Field(description='"BUY", "SELL", or "HOLD"')
    confidence_score: int = Field(ge=1, le=10, description="1-10")
    target_price: float
    stop_loss: float
    quantity: int = Field(default=0, description="shares to buy given budget")
    holding_period_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description="predicted days to hold before selling to capture the swing move",
    )
    exit_plan: str = Field(
        default="",
        description="when and how to exit: target reached, stop-loss hit, time-based exit, or momentum exhaustion",
    )
    rationale: str


SYSTEM_PROMPT = """
You are a swing trading analyst for the Indian stock market (NSE).
Analyze technical indicators, candlestick patterns, fundamental metrics, news
sentiment, and gap-up / gap-down open risk for a 5-10 day holding period.

Return a strict JSON object matching the schema. For BUY, quantity is the
number of shares that fit within the available budget (floor of
budget / entry price). For SELL or HOLD, quantity may be 0.
Be conservative; do not invent prices. Use the provided data only.

For every recommendation, predict holding_period_days: the expected number of
days to hold before selling to capture the swing profit, aligned with the
5-10 day horizon. In exit_plan, state the concrete exit trigger: the first
condition among (a) target price reached, (b) stop loss hit, (c) time-based
exit after holding_period_days, or (d) momentum exhaustion. Justify your
timing from the indicators (distance to target, RSI levels, support
breakdown). For SELL/HOLD of an existing position, exit_plan describes when
to exit the position in full.
"""


class GeminiAgent:
    def __init__(self, api_key: str = None):
        self.client = genai.Client(api_key=api_key or config.GEMINI_API_KEY)
        self.model = config.GEMINI_MODEL

    def _generate(self, content: str, schema) -> StockRecommendation:
        for attempt in range(5):
            try:
                resp = self.client.models.generate_content(
                    model=self.model,
                    contents=content,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_schema=schema,
                        thinking_config=types.ThinkingConfig(thinking_level="high"),
                    ),
                )
                parsed = resp.parsed
                if parsed is None:
                    raise ValueError(f"Gemini returned no parsed object: {resp.text}")
                return parsed
            except (ClientError, ServerError) as exc:
                message = str(exc)
                code = getattr(exc, "status_code", None)
                if code is None:
                    code = getattr(exc, "code", None)
                if code == 429 or "429" in message:
                    if attempt == 4:
                        raise QuotaExceededError(
                            "Gemini daily quota exceeded (free tier ~20 calls/day). "
                            "Add a GEMINI_API_KEY on a paid plan or retry tomorrow."
                        )
                    delay = min(60, 15 * (attempt + 1))
                    log.warning("Gemini quota exhausted, retrying in %ss", delay)
                elif code == 503 or "UNAVAILABLE" in message:
                    if attempt == 4:
                        raise RuntimeError(
                            f"Gemini model unavailable after retries for {self.model}: {message}"
                        )
                    delay = min(60, 15 * (attempt + 1))
                    log.warning("Gemini model busy (503), retrying in %ss", delay)
                else:
                    raise
                time.sleep(delay)
                continue
        raise QuotaExceededError("Gemini quota retries exhausted")

    def analyze_stock(
        self,
        ticker: str,
        technicals: dict,
        news: str,
        budget: float = 0.0,
        holding_context: Optional[dict] = None,
        fundamentals: str = "",
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> StockRecommendation:
        context_lines = []
        if holding_context:
            context_lines.append(
                f"Current holding: qty={holding_context.get('quantity')}, "
                f"avg_price={holding_context.get('avg_price')}, "
                f"ltp={holding_context.get('ltp')}"
            )
        context_lines.append(f"Available budget: INR {budget}")
        context_lines.append("Technicals (latest bar):")
        context_lines.append(str(technicals))
        if fundamentals:
            context_lines.append("Fundamentals:")
            context_lines.append(fundamentals)
        context_lines.append("Recent news:")
        context_lines.append(news)
        content = "\n".join(context_lines)
        if on_log:
            on_log("prompt", content)
        result = self._generate(content, StockRecommendation)
        if on_log:
            on_log("response", result.model_dump_json())
        return result

    def chat(self, question: str, context: str) -> str:
        prompt = (
            "You are assisting with today's swing trading analysis. "
            "Use the provided context to answer.\n\nCONTEXT:\n"
            + context
            + "\n\nQUESTION:\n"
            + question
        )
        resp = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level="high"),
            ),
        )
        return resp.text or ""
