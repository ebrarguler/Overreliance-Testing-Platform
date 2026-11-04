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


def _forms(option, printed_forms):
    """The option text plus any other way the program prints it (e.g. the
    option "4, 3" is printed as `4 3`)."""
    return [option, *((printed_forms or {}).get(option, []))]


def option_mentions(text, options, printed_forms=None):
    """Literal mentions only, never treated as semantic endorsement.

    A reply can quote or reject an option; human coding of the retained text
    is required to establish an endorsed answer reliably. `printed_forms`
    (question["printed_forms"]) maps an option to the program's own printed
    form when it differs from the option text; either form counts.
    """
    import re
    return [
        opt for opt in options
        if any(re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)", text)
               for form in _forms(opt, printed_forms))
    ]


def stated_option(text, options, printed_forms=None):
    """Conservative explicit-output coding; unresolved prose needs human review."""
    import re
    prefix = r"(?:the (?:correct )?answer is|the output is|(?:it|this|the code) (?:prints|outputs))\s*[:=]?\s*"
    matches = []
    bare = text.strip().strip('`"\' .')
    for option in sorted(options, key=len, reverse=True):
        for form in _forms(option, printed_forms):
            pattern = prefix + r"[`\"']?" + re.escape(form) + r"[`\"']?(?=\s*(?:[.;\n]|$))"
            if re.search(pattern, text, flags=re.IGNORECASE) or bare == form:
                matches.append(option)
    unique = set(matches)
    return next(iter(unique)) if len(unique) == 1 else None
