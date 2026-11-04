"""Unit tests for Antigravity model discovery and smart task-tier routing."""

from pathlib import Path

from agy_orchestrator.models import ModelRegistry, parse_models_output

SAMPLE_AGY_MODELS_STDOUT = """\
Fetching available models...
gemini-4-argon-medium\tGemini 4 Argon (Medium)
gemini-4-argon-high\tGemini 4 Argon (High)
gemini-4-argon-low\tGemini 4 Argon (Low)
gemini-3.8-flash-high\tGemini 3.8 Flash (High)
gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)
gemini-3.8-flash-low\tGemini 3.8 Flash (Low)
claude-opus-5-5-low\tClaude Opus 5.5 (Low)
claude-opus-5-5-medium\tClaude Opus 5.5 (Medium)
claude-opus-5-5-high\tClaude Opus 5.5 (High)
claude-sonnet-5-5-low\tClaude Sonnet 5.5 (Low)
claude-sonnet-5-5-medium\tClaude Sonnet 5.5 (Medium)
claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)
gpt-oss-120b-medium\tGPT-OSS 120B (Medium)
"""


def test_parse_models_output_ignores_banner_and_extracts_all_models() -> None:
    catalog = parse_models_output(SAMPLE_AGY_MODELS_STDOUT)
    assert len(catalog) == 13
    assert catalog[0].model_id == "gemini-4-argon-medium"
    assert catalog[0].display_name == "Gemini 4 Argon (Medium)"
    assert "coding" in catalog[0].recommended_tiers


def test_tier_routing_picks_quota_efficient_models() -> None:
    catalog = parse_models_output(SAMPLE_AGY_MODELS_STDOUT)
    registry = ModelRegistry(Path("agy"))

    assert registry.resolve_from_catalog(catalog, tier="trivial") == "gemini-3.8-flash-low"
    assert registry.resolve_from_catalog(catalog, tier="writing") == "gemini-3.8-flash-high"
    assert registry.resolve_from_catalog(catalog, tier="coding") == "gemini-3.8-flash-high"
    assert registry.resolve_from_catalog(catalog, tier="complex") == "gemini-4-argon-high"


def test_explicit_model_shorthand_resolution() -> None:
    catalog = parse_models_output(SAMPLE_AGY_MODELS_STDOUT)
    registry = ModelRegistry(Path("agy"))

    assert (
        registry.resolve_from_catalog(catalog, explicit_model="sonnet-high")
        == "claude-sonnet-5-5-high"
    )
    assert (
        registry.resolve_from_catalog(catalog, explicit_model="opus-medium")
        == "claude-opus-5-5-medium"
    )
    assert (
        registry.resolve_from_catalog(catalog, explicit_model="gpt-oss-120b-medium")
        == "gpt-oss-120b-medium"
    )


def test_empty_catalog_falls_back_gracefully() -> None:
    registry = ModelRegistry(Path("agy"))
    assert registry.resolve_from_catalog([], tier="trivial") == "gemini-3.8-flash-low"
    assert registry.resolve_from_catalog([], explicit_model="custom-model") == "custom-model"
