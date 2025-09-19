from datetime import datetime
import libtorrent as lt

# --- In-memory State ---
# This dictionary will hold the state of all torrents managed by the application.
# It's a simple, in-memory database.
active_torrents = {}

# Structure for each torrent in active_torrents:
# {
#   "torrent_hash": {
#     "handle": lt.torrent_handle,
#     "info": lt.torrent_info,
#     "status": "warming_cache" | "paused" | "downloading" | "seeding" | "error",
#     "name": str,
#     "files": list,
#     "added_at": datetime,
#     "last_accessed_at": datetime,
#     "error": str | None,
#     "hls_process": asyncio.subprocess.Process | None,
#     "hls_last_accessed": datetime | None
#   }
# }

# --- libtorrent Session ---
# Global session object for libtorrent
ses = None

def get_session():
    """Returns the global libtorrent session, creating it if it doesn't exist."""
    global ses
    if ses is None:
        from src.config import PORT
        ses = lt.session({
            'listen_interfaces': f'0.0.0.0:{PORT + 10}',
            'alert_mask': lt.alert.category_t.all_categories,
            'user_agent': 'plays96/1.0.0',
            'download_rate_limit': 0,
            'upload_rate_limit': 0,
            'connections_limit': 200,
            'active_dht_limit': 88,
            'active_tracker_limit': 1600,
            'active_lsd_limit': 60,
            'active_limit': 500,
        })
    return ses
