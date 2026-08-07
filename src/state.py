from datetime import datetime
import logging
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

logger = logging.getLogger(__name__)


def _start_optional_service(session, service_name):
    service = getattr(session, service_name, None)
    if not callable(service):
        return

    try:
        service()
        logger.info("Started libtorrent service: %s", service_name)
    except Exception as exc:
        logger.warning("Failed to start libtorrent service %s: %s", service_name, exc)


def _bootstrap_peer_discovery(session):
    routers = [
        ("router.bittorrent.com", 6881),
        ("router.utorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
        ("router.bitcomet.com", 6881),
    ]

    try:
        session.start_dht()
        logger.info("Started libtorrent DHT")
    except Exception as exc:
        logger.warning("Failed to start libtorrent DHT: %s", exc)

    for host, port in routers:
        try:
            session.add_dht_router(host, port)
        except Exception as exc:
            logger.warning("Failed to add DHT router %s:%s: %s", host, port, exc)

    _start_optional_service(session, "start_lsd")
    _start_optional_service(session, "start_upnp")
    _start_optional_service(session, "start_natpmp")

def get_session():
    """Returns the global libtorrent session, creating it if it doesn't exist."""
    global ses
    if ses is None:
        from src.config import TORRENT_PORT
        ses = lt.session({
            'listen_interfaces': f'0.0.0.0:{TORRENT_PORT}',
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
        _bootstrap_peer_discovery(ses)
    return ses
