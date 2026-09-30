"""Minimal .env loading.

No new dependency: the files we need to support are simple KEY=VALUE lists.
Already-set environment variables win over the file, so real env vars can
always override .env (standard dotenv behavior).
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("BlenderMCPServer")

ENV_FILE_ENV = "BLENDERMCP_ENV_FILE"


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

    Looks at BLENDERMCP_ENV_FILE first, then .env in the current working
    directory. Existing environment variables are never overwritten.
    Returns the loaded path, or None when no file was found.
    """
    candidate = path or os.getenv(ENV_FILE_ENV) or ".env"
    env_path = Path(candidate)
    if not env_path.is_file():
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
