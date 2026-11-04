"""Reproducibility metadata; no credentials or environment dumps."""
import hashlib
import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def runtime_metadata():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = os.getenv("APP_COMMIT", "unknown"), None
    files = sorted((root / "app").rglob("*.py")) + sorted((root / "app" / "templates").glob("*.html"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return {"app_commit": commit, "app_dirty": dirty, "app_source_hash": digest.hexdigest(),
            "study_phase": os.getenv("STUDY_PHASE", "pilot"), "protocol_version": "durable_natural_v1"}


def prompt_hash(prompt):
    return hashlib.sha256(prompt.encode()).hexdigest()


def option_mentions(text, options):
    """Literal mentions only, never treated as semantic endorsement.

    A reply can quote or reject an option; human coding of the retained text
    is required to establish an endorsed answer reliably.
    """
    import re
    return [opt for opt in options if re.search(r"(?<!\w)" + re.escape(opt) + r"(?!\w)", text)]


def stated_option(text, options):
    """Conservative explicit-output coding; unresolved prose needs human review."""
    import re
    prefix = r"(?:the (?:correct )?answer is|the output is|(?:it|this|the code) (?:prints|outputs))\s*[:=]?\s*"
    matches = []
    for option in sorted(options, key=len, reverse=True):
        pattern = prefix + r"[`\"']?" + re.escape(option) + r"[`\"']?(?=\s*(?:[.;\n]|$))"
        if re.search(pattern, text, flags=re.IGNORECASE):
            matches.append(option)
    if text.strip().strip('`"\' .') in options:
        matches.append(text.strip().strip('`"\' .'))
    unique = set(matches)
    return next(iter(unique)) if len(unique) == 1 else None
