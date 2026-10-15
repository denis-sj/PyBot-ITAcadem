"""Checked Polza catalog tariffs for conservative paid-evaluation accounting."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field


class ModelTariff(BaseModel):
    """Ruble prices per million billed tokens for a known provider or an upper bound."""

    provider: str = "task_supplied_tariff"
    input_price: Decimal = Decimal("5.29")
    output_price: Decimal = Decimal("21.17")
    cached_input_price: Decimal | None = None

    def estimate(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> Decimal:
        """Use an explicit cache tariff only when its serving provider is known."""
        cached_tokens = min(max(cached_tokens, 0), input_tokens)
        cache_price = self.cached_input_price if self.cached_input_price is not None else self.input_price
        return (
            Decimal(input_tokens - cached_tokens) * self.input_price
            + Decimal(cached_tokens) * cache_price
            + Decimal(output_tokens) * self.output_price
        ) / 1_000_000


class CatalogPricing(BaseModel):
    prompt_per_million: Decimal
    completion_per_million: Decimal
    input_cache_read_per_million: Decimal | None = None


class CatalogProvider(BaseModel):
    name: str
    pricing: CatalogPricing
    supported_parameters: list[str] = Field(default_factory=list)

    def tariff(self) -> ModelTariff:
        return ModelTariff(
            provider=self.name,
            input_price=self.pricing.prompt_per_million,
            output_price=self.pricing.completion_per_million,
            cached_input_price=self.pricing.input_cache_read_per_million,
        )


class ProviderCatalog(BaseModel):
    """The public catalog snapshot; no API credentials or user information."""

    id: str
    providers: list[CatalogProvider]

    @classmethod
    def load(cls, path: Path) -> ProviderCatalog:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def tariff(self, provider_name: str | None = None) -> ModelTariff:
        for provider in self.providers:
            if provider.name == provider_name:
                return provider.tariff()
        return ModelTariff(
            provider="unknown_provider_catalog_upper_bound",
            input_price=max(provider.pricing.prompt_per_million for provider in self.providers),
            output_price=max(provider.pricing.completion_per_million for provider in self.providers),
        )
