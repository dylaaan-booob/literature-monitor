"""Non-sensitive, process-local Connector access observations (SPEC §42.6)."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from literature_monitor.url_safety import normalize_public_http_url


class AccessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    landing_origin: str | None = Field(default=None, max_length=253)
    service_origin: str | None = Field(default=None, max_length=253)
    reason: Literal["unknown_service", "no_verified_route", "preparing", "prepared",
                    "manual_challenge", "redirect_loop", "hop_limit", "unsafe_destination",
                    "access_timeout", "access_failed", "service_deferred"]
    # 尚无已验证的会话/页面指标；导航不能使这四项从 unknown 升级。
    idp_session: Literal["unknown"] = "unknown"
    sp_session: Literal["unknown"] = "unknown"
    authentication: Literal["unknown"] = "unknown"
    entitlement: Literal["unknown"] = "unknown"

    @field_validator("landing_origin", "service_origin")
    @classmethod
    def origin_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalize_public_http_url(value)
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.path or parsed.query or parsed.fragment
                or parsed.port is not None or value != f"https://{parsed.hostname}"):
            raise ValueError("access observations accept only public HTTPS origins")
        return value
