"""Versioned prompts shared by API and worker processes."""

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def prompt(name: str) -> str:
    if name not in {"classify_v1", "answer_v1", "draft_v1"}:
        raise ValueError("Unknown registered prompt.")
    return (Path(__file__).with_name("prompts") / f"{name}.md").read_text()
