"""Versioned prompt loader.

Prompt files live next to this module and are named ``<stage>_v<N>.md``. Callers ask for
the logical name (e.g. ``"stage1_diagnostic"``) and always get the highest version, plus
the version string and a sha256 of the file bytes so every LLM call can be audited against
the exact prompt text that produced it.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent

_VERSION_RE = re.compile(r"_v(\d+)\.md$")


@dataclass(frozen=True)
class PromptInfo:
    text: str
    version: str  # e.g. "v1"
    sha256: str  # hex digest of the raw file bytes


@lru_cache(maxsize=None)
def load_prompt(name: str) -> PromptInfo:
    """Load the highest-versioned prompt file for ``name``.

    Cached per logical name; tests that edit prompt files on disk must call
    ``load_prompt.cache_clear()`` after changing them.
    """
    candidates: list[tuple[int, Path]] = []
    for path in PROMPTS_DIR.glob(f"{name}_v*.md"):
        match = _VERSION_RE.search(path.name)
        if match is None:
            continue
        version_num = int(match.group(1))
        if path.name != f"{name}_v{version_num}.md":  # reject e.g. name_v1_draft.md
            continue
        candidates.append((version_num, path))
    if not candidates:
        raise FileNotFoundError(f"no prompt files match {name!r}_v<N>.md in {PROMPTS_DIR}")
    version_num, path = max(candidates)
    raw = path.read_bytes()
    return PromptInfo(
        text=raw.decode("utf-8"),
        version=f"v{version_num}",
        sha256=hashlib.sha256(raw).hexdigest(),
    )
