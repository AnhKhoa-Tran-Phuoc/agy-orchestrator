"""Model catalog discovery and smart task-tier routing for Antigravity CLI models."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import os
from pathlib import Path
from typing import Literal

TaskTier = Literal["trivial", "writing", "coding", "complex"]

VALID_TIERS: tuple[TaskTier, ...] = ("trivial", "writing", "coding", "complex")

TIER_DESCRIPTIONS: dict[TaskTier, str] = {
    "trivial": "Fastest & lowest quota usage: formatting, boilerplate, translations, data conversion, reading/summarizing large files.",
    "writing": "High-fluency long-form generation: articles, documentation, READMEs, tutorials, docstrings, localization.",
    "coding": "Balanced multi-file engineering: feature implementation, unit tests, CRUD modules, standard bug fixes.",
    "complex": "Deep reasoning & tricky engineering: concurrency bugs, complex refactors, algorithms, multi-module debugging.",
}

# Ordered preference patterns (all substrings in a tuple must match model_id in lowercase)
TIER_PREFERENCE_RULES: dict[TaskTier, list[tuple[str, ...]]] = {
    "trivial": [
        ("flash", "low"),
        ("flash", "medium"),
        ("gpt-oss",),
        ("argon", "low"),
        ("sonnet", "low"),
        ("flash",),
    ],
    "writing": [
        ("flash", "high"),
        ("flash", "medium"),
        ("gpt-oss",),
        ("argon", "low"),
        ("argon", "medium"),
        ("sonnet", "low"),
    ],
    "coding": [
        ("argon", "medium"),
        ("sonnet", "medium"),
        ("flash", "high"),
        ("sonnet", "high"),
        ("argon", "high"),
    ],
    "complex": [
        ("argon", "high"),
        ("sonnet", "high"),
        ("opus", "high"),
        ("opus", "medium"),
        ("argon", "medium"),
    ],
}

FALLBACK_MODELS_BY_TIER: dict[TaskTier, str] = {
    "trivial": "gemini-3.8-flash-low",
    "writing": "gemini-3.8-flash-high",
    "coding": "gemini-4-argon-medium",
    "complex": "gemini-4-argon-high",
}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Represents a single model reported by `agy models`."""

    model_id: str
    display_name: str
    recommended_tiers: list[TaskTier]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def infer_recommended_tiers(model_id: str) -> list[TaskTier]:
    """Tag a model ID with the task tiers it naturally suits."""
    mid = model_id.lower()
    tiers: list[TaskTier] = []
    if "flash" in mid and "low" in mid:
        tiers.append("trivial")
    elif "flash" in mid:
        tiers.extend(["writing", "trivial"])
    elif "gpt-oss" in mid:
        tiers.extend(["trivial", "writing"])
    elif ("argon" in mid or "sonnet" in mid) and "medium" in mid:
        tiers.append("coding")
    elif ("argon" in mid or "sonnet" in mid or "opus" in mid) and "high" in mid:
        tiers.append("complex")
    elif "opus" in mid:
        tiers.append("complex")
    elif "low" in mid:
        tiers.append("trivial")
    return tiers


def parse_models_output(raw_output: str) -> list[ModelInfo]:
    """Parse stdout from `agy models` into structured `ModelInfo` entries."""
    models: list[ModelInfo] = []
    seen: set[str] = set()

    for raw_line in raw_output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(("fetching ", "warning:", "available models", "---")):
            continue

        if "\t" in line:
            model_id, display_name = line.split("\t", 1)
        elif "  " in line:
            parts = [p.strip() for p in line.split("  ") if p.strip()]
            if len(parts) < 2:
                continue
            model_id, display_name = parts[0], parts[1]
        else:
            # Only accept bare single-token lines if they look like slug identifiers
            if " " not in line and "-" in line:
                model_id, display_name = line, line
            else:
                continue

        model_id = model_id.strip()
        display_name = display_name.strip()
        if not model_id or model_id in seen:
            continue

        seen.add(model_id)
        models.append(
            ModelInfo(
                model_id=model_id,
                display_name=display_name,
                recommended_tiers=infer_recommended_tiers(model_id),
            )
        )

    return models


class ModelRegistry:
    """Discovers available Antigravity models and resolves tier/alias requests."""

    def __init__(self, agy_bin: Path) -> None:
        self._agy_bin = agy_bin
        self._cached_models: list[ModelInfo] | None = None

    async def list_models(self, refresh: bool = False) -> list[ModelInfo]:
        """Fetch available models via `agy models`, caching the result in memory."""
        if self._cached_models is not None and not refresh:
            return self._cached_models

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        proc = await asyncio.create_subprocess_exec(
            str(self._agy_bin),
            "models",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
        raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
        parsed = parse_models_output(raw_stdout)
        if parsed:
            self._cached_models = parsed
        return parsed

    def resolve_from_catalog(
        self,
        catalog: list[ModelInfo],
        tier: TaskTier = "coding",
        explicit_model: str | None = None,
    ) -> str:
        """Select the best matching `model_id` given an explicit override or a `TaskTier`."""
        normalized_tier: TaskTier = tier if tier in VALID_TIERS else "coding"

        if explicit_model:
            requested = explicit_model.strip()
            if not catalog:
                return requested

            # 1. Exact match
            for item in catalog:
                if item.model_id == requested:
                    return item.model_id

            # 2. Case-insensitive match
            req_lower = requested.lower()
            for item in catalog:
                if item.model_id.lower() == req_lower or item.display_name.lower() == req_lower:
                    return item.model_id

            # 3. Token-subset shorthand match (e.g. "flash-low" or "sonnet-high")
            tokens = [tok for tok in req_lower.replace("_", "-").split("-") if tok]
            if tokens:
                for item in catalog:
                    mid_lower = item.model_id.lower()
                    if all(tok in mid_lower for tok in tokens):
                        return item.model_id

            # Fall back to exact string supplied by caller (allows brand-new models)
            return requested

        if not catalog:
            return FALLBACK_MODELS_BY_TIER[normalized_tier]

        for required_tokens in TIER_PREFERENCE_RULES[normalized_tier]:
            for item in catalog:
                mid_lower = item.model_id.lower()
                if all(tok in mid_lower for tok in required_tokens):
                    return item.model_id

        return catalog[0].model_id

    async def resolve_model(
        self,
        tier: TaskTier = "coding",
        explicit_model: str | None = None,
    ) -> str:
        """Resolve a model ID using the cached/live `agy models` catalog."""
        try:
            catalog = await self.list_models(refresh=False)
        except Exception:
            catalog = []
        return self.resolve_from_catalog(catalog, tier=tier, explicit_model=explicit_model)
