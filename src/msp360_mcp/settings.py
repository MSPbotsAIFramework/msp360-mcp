# Copyright 2026 MSPbots.ai
# SPDX-License-Identifier: Apache-2.0
"""Process-level settings.

MSP360 credentials are never read from here — they travel per-request via
the X-Msp360-Username / X-Msp360-Password headers (see middleware.py). This
class only ever holds non-credential, non-tenant configuration.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080


def get_settings() -> Settings:
    return Settings()
