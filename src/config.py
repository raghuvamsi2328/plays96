import os
import logging

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

PORT = int(os.getenv("PORT", 6991))
CLEANUP_INTERVAL_HOURS = 12
DOWNLOAD_PATH = "downloads"
HLS_PATH = "hls"
WARM_CACHE_TIMEOUT_MINUTES = 20
