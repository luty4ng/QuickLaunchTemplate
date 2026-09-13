"""Billing 的设置：Stripe 凭据、价格映射、webhook 容差。

从骨架的 `app/config.py` 搬出来——骨架不该知道"档位是拿 Stripe 价格换来的"这件事。
环境变量名一个都没变（`STRIPE_SECRET_KEY` 等），所以现有 `.env` 与 CI 的假密钥都不用改。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BillingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Empty everywhere except the deployment (and never in the repository). The
    # billing endpoints answer 503 when these are missing rather than pretending
    # to work, and CI exercises the same code path with an injected fake gateway.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_plus: str = ""
    stripe_price_pro: str = ""
    # Point the provider at something other than api.stripe.com. Only used to
    # exercise the real HTTP code path against scripts/fake_stripe.py; empty means
    # the official API.
    stripe_api_base: str = ""
    # Where Stripe sends the browser back after Checkout / the customer portal.
    # Relative paths are resolved against the request's own origin when unset.
    stripe_success_path: str = "/?billing=success"
    stripe_cancel_path: str = "/?billing=cancelled"
    # Tolerance for webhook signature timestamps. Stripe's default is 300s; the
    # server's clock must be roughly right or every event is rejected.
    stripe_webhook_tolerance: int = Field(default=300, ge=30)

    @property
    def stripe_enabled(self) -> bool:
        """True only when the API can actually talk to Stripe."""
        return bool(self.stripe_secret_key and self.stripe_price_plus and self.stripe_price_pro)

    @property
    def stripe_uses_official_api(self) -> bool:
        return not self.stripe_api_base

    def price_id_for(self, plan: str) -> str:
        return {"plus": self.stripe_price_plus, "pro": self.stripe_price_pro}.get(plan, "")

    def plan_for_price(self, price_id: str) -> str | None:
        """Map a Stripe price back to a plan; None when it is not ours."""
        if price_id and price_id == self.stripe_price_plus:
            return "plus"
        if price_id and price_id == self.stripe_price_pro:
            return "pro"
        return None


@lru_cache(maxsize=1)
def get_billing_settings() -> BillingSettings:
    return BillingSettings()
