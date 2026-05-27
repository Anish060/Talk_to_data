import logging
from pathlib import Path

# ------------------------------------------------------------------
# Determine project root (two levels up from this file) and create a
# "logs" directory if it does not exist.
# ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Log file name – you can change the naming scheme if you wish.
LOG_FILE = LOG_DIR / "pipeline_main.log"

# Remove any existing handlers that may have been added by other modules
# (e.g., earlier calls to logging.basicConfig). This ensures that our
# configuration is applied even when other parts of the code called
# ``logging.basicConfig`` before we import this helper.
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

# Configure the root logger to write to both the file and stdout.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Export a module‑level logger that other modules can import directly.
logger = logging.getLogger(__name__)
