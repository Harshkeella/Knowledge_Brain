"""Async token bucket, sized in tokens-per-minute, for pacing LLM calls.

Reactive 429-then-backoff wastes a round trip and the retry wait on every
burst; spending the budget at the rate it refills avoids the 429 entirely.
"""

import asyncio
import time


class TokenBucket:
    def __init__(self, tokens_per_minute: int):
        self.capacity = float(tokens_per_minute)
        self.rate = self.capacity / 60.0
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def resize(self, tokens_per_minute: int) -> None:
        """Re-size to the real budget. Groq's own 429 body reports the model's
        TPM limit, which beats keeping a config value in sync by hand."""
        self.capacity = float(tokens_per_minute)
        self.rate = self.capacity / 60.0
        self._tokens = min(self._tokens, self.capacity)

    async def acquire(self, tokens: float) -> None:
        """Wait until `tokens` are available, then spend them. A request bigger
        than the whole bucket drains it rather than waiting forever."""
        tokens = min(float(tokens), self.capacity)
        # ponytail: one lock serializes waiters, so callers go FIFO not fair-share.
        # Fine at MAX_ASYNC_LLM=2; revisit if many roles share one bucket.
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)


def estimate_tokens(prompt: str, system_prompt: str | None, output_allowance: int = 1000) -> int:
    """~4 chars/token, plus room for the response — Groq's TPM counts both."""
    return (len(prompt) + len(system_prompt or "")) // 4 + output_allowance
