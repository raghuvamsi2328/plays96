import os
import logging

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



PORT = int(os.getenv("PORT", 6991))
CLEANUP_INTERVAL_HOURS = 12
# Use absolute paths for downloads and hls directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", os.path.join(BASE_DIR, "downloads"))
HLS_PATH = os.getenv("HLS_PATH", os.path.join(BASE_DIR, "hls"))
WARM_CACHE_TIMEOUT_MINUTES = 20

# Log the resolved paths at import time for debugging
logging.info(f"[CONFIG] DOWNLOAD_PATH: {DOWNLOAD_PATH}")
logging.info(f"[CONFIG] HLS_PATH: {HLS_PATH}")
