"""Central configuration for LoadPilot.

Everything that can differ between a laptop, Colab and a real dispatch
account is read from environment variables here, so the rest of the code
never touches ``os.environ`` directly.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEMO_LOADS_PATH = DATA_DIR / "demo_loads.json"
CITIES_PATH = DATA_DIR / "cities.json"


def _env_path(name: str, default: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else default


# A real export / API snapshot can be dropped here. If the file is absent or
# empty the agent transparently falls back to the bundled demo board.
LOADS_FILE = _env_path("SUPERDISPATCH_LOADS_FILE", DATA_DIR / "loads.json")
WATCHLIST_FILE = _env_path("WATCHLIST_FILE", DATA_DIR / "watchlist.json")

# Optional live integration. SuperDispatch API access is subscription based;
# the endpoint is configurable because accounts receive different base URLs.
SUPERDISPATCH_API_URL = os.getenv("SUPERDISPATCH_API_URL", "").strip()
SUPERDISPATCH_API_TOKEN = os.getenv("SUPERDISPATCH_API_TOKEN", "").strip()
SUPERDISPATCH_AUTH_SCHEME = os.getenv("SUPERDISPATCH_AUTH_SCHEME", "Bearer").strip()
SUPERDISPATCH_TIMEOUT = float(os.getenv("SUPERDISPATCH_TIMEOUT", "10"))
SUPERDISPATCH_ALLOW_DEMO_FALLBACK = os.getenv(
    "SUPERDISPATCH_ALLOW_DEMO_FALLBACK", "0"
).strip().lower() in {"1", "true", "yes"}

# Default cost model for a 3-car open trailer. All values are intentionally
# configurable from the tool call so the model can use the driver's numbers.
DEFAULT_MPG = float(os.getenv("DEFAULT_MPG", "6.5"))
DEFAULT_DIESEL_PRICE = float(os.getenv("DEFAULT_DIESEL_PRICE", "3.75"))
DEFAULT_FIXED_CPM = float(os.getenv("DEFAULT_FIXED_CPM", "0.35"))
DEFAULT_TARGET_RPM = float(os.getenv("DEFAULT_TARGET_RPM", "0.45"))

# Model defaults. Qwen2.5-3B-Instruct fits comfortably on a free Colab T4.
DEFAULT_MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen2.5-3B-Instruct")
MAX_NEW_TOKENS = int(os.getenv("MAX_NEW_TOKENS", "512"))
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "8192"))
