
import os
import logging
from pathlib import Path

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PORT = int(os.getenv("PORT", 6991))
CLEANUP_INTERVAL_HOURS = 12
# Always resolve to absolute paths
BASE_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_PATH = Path(os.getenv("DOWNLOAD_PATH", str(BASE_DIR / "downloads"))).resolve()
HLS_PATH = Path(os.getenv("HLS_PATH", str(BASE_DIR / "hls"))).resolve()
WARM_CACHE_TIMEOUT_MINUTES = 20

# Log the resolved paths at import time for debugging
logging.info(f"[CONFIG] DOWNLOAD_PATH: {DOWNLOAD_PATH}")
logging.info(f"[CONFIG] HLS_PATH: {HLS_PATH}")
