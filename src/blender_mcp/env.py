"""Minimal .env loading.

No new dependency: the files we need to support are simple KEY=VALUE lists.
Already-set environment variables win over the file, so real env vars can
always override .env (standard dotenv behavior).
"""

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("BlenderMCPServer")

ENV_FILE_ENV = "BLENDERMCP_ENV_FILE"


def _executable_env_path() -> Optional[Path]:
    """A .env next to the frozen executable, or None when running from source.

    A double-clicked PyInstaller build gets the user's home directory as its
    working directory, so a plain cwd lookup never finds the .env that ships
    beside the binary.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / ".env"


def _parse_line(line: str) -> Optional[tuple[str, str]]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_dotenv(path: Optional[str] = None) -> Optional[Path]:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Lookup order: the explicit path / BLENDERMCP_ENV_FILE, then a .env next
    to the frozen executable, then .env in the current working directory.
    Existing environment variables are never overwritten.
    Returns the loaded path, or None when no file was found.
    """
    explicit = path or os.getenv(ENV_FILE_ENV)
    candidates: List[Path] = [Path(explicit)] if explicit else []
    if not explicit:
        exe_env = _executable_env_path()
        if exe_env is not None:
            candidates.append(exe_env)
        candidates.append(Path(".env"))

    env_path = next((c for c in candidates if c.is_file()), None)
    if env_path is None:
        return None

    loaded = 0
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_line(line)
            if parsed is None:
                continue
            key, value = parsed
            if key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except OSError as e:
        logger.warning(f"Could not read {env_path}: {e}")
        return None

    logger.info(f"Loaded {loaded} variables from {env_path}")
    return env_path
