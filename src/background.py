import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta

import libtorrent as lt

from src.config import (
    DOWNLOAD_PATH,
    HLS_PATH,
    WARM_CACHE_TIMEOUT_MINUTES,
)
from src.state import active_torrents, get_session


async def alert_listener():
    """Listens for and processes libtorrent alerts."""
    ses = get_session()
    while True:
        alerts = ses.pop_alerts()
        for alert in alerts:
            if isinstance(alert, lt.metadata_received_alert):
                h = alert.handle
                info_hash = str(h.info_hash())
                if info_hash in active_torrents:
                    info = h.get_torrent_info()
                    files = []
                    for i in range(info.num_files()):
                        file_entry = info.file_at(i)
                        files.append({
                            "index": i,
                            "name": file_entry.path,
                            "size": file_entry.size,
                            "progress": 0.0,
                            "is_video": any(file_entry.path.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov'])
                        })
                    active_torrents[info_hash]["info"] = info
                    active_torrents[info_hash]["name"] = info.name()
                    active_torrents[info_hash]["files"] = files
                    active_torrents[info_hash]["status"] = "downloading"
                    logging.info(f"Metadata received for {info.name()}")

            elif isinstance(alert, lt.torrent_finished_alert):
                h = alert.handle
                info_hash = str(h.info_hash())
                if info_hash in active_torrents:
                    active_torrents[info_hash]["status"] = "completed"
                logging.info(f"Torrent finished: {info_hash}")

            elif isinstance(alert, lt.torrent_error_alert):
                h = alert.handle
                info_hash = str(h.info_hash())
                if info_hash in active_torrents:
                    active_torrents[info_hash]["status"] = "error"
                    active_torrents[info_hash]["error"] = alert.error.message()
                logging.error(f"Torrent error for {info_hash}: {alert.error.message()}")

        await asyncio.sleep(1)


async def cleanup_inactive_streams():
    """
    Periodically checks for inactive HLS streams.
    If a stream is inactive for too long, it kills the ffmpeg process,
    deletes the HLS files, and reverts the torrent to a paused state.
    """
    while True:
        await asyncio.sleep(60)  # Check every minute
        now = datetime.now()
        
        for torrent_id, torrent_info in list(active_torrents.items()):
            last_accessed = torrent_info.get("hls_last_accessed")
            if last_accessed and (now - last_accessed) > timedelta(minutes=WARM_CACHE_TIMEOUT_MINUTES):
                logging.info(f"HLS stream for {torrent_id} is inactive. Cleaning up.")
                
                # Kill ffmpeg process
                process = torrent_info.get("hls_process")
                if process and process.returncode is None:
                    try:
                        process.terminate()
                        await process.wait()
                        logging.info(f"Terminated ffmpeg process for {torrent_id}")
                    except ProcessLookupError:
                        pass # Process already dead
                
                torrent_info["hls_process"] = None
                torrent_info["hls_last_accessed"] = None
                
                # Delete HLS files
                hls_output_dir = os.path.join(HLS_PATH, torrent_id)
                if os.path.exists(hls_output_dir):
                    shutil.rmtree(hls_output_dir)
                    logging.info(f"Deleted HLS directory: {hls_output_dir}")
                    
                # Don't pause the torrent - let it continue downloading


async def log_download_speeds(interval_seconds=5):
    """Periodically logs torrent download rates for stream diagnostics."""
    while True:
        await asyncio.sleep(interval_seconds)
        for torrent_id, torrent_info in list(active_torrents.items()):
            handle = torrent_info.get("handle")
            if not handle or not handle.is_valid():
                continue

            try:
                status = handle.status()
            except RuntimeError:
                continue

            logging.info(
                "[%s] rate=%.1f KB/s peers=%s state=%s progress=%.1f%%",
                torrent_id,
                status.download_rate / 1024,
                status.num_peers,
                status.state,
                status.progress * 100,
            )
