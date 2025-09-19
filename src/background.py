import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta

import libtorrent as lt

from src.config import (
    DOWNLOAD_PATH,
    HLS_PATH,
    WARM_CACHE_SIZE_MB,
    WARM_CACHE_TIMEOUT_MINUTES,
)
from src.state import active_torrents, get_session
from src.utils import get_largest_video_file


async def alert_listener():
    """
    Listens for libtorrent alerts and updates torrent status.
    Manages the transition from metadata -> warm cache -> full download.
    """
    ses = get_session()
    while True:
        alerts = ses.pop_alerts()
        for alert in alerts:
            if isinstance(alert, lt.metadata_received_alert):
                handle = alert.handle
                if not handle.is_valid() or str(handle.info_hash()).lower() not in active_torrents:
                    continue
                
                torrent_hash = str(handle.info_hash()).lower()
                torrent_info = active_torrents[torrent_hash]
                
                logging.info(f"Metadata received for {torrent_hash}")
                torrent_info["status"] = "downloading_warm_cache"
                
                # Find the largest video file and prioritize it
                ti = handle.get_torrent_info()
                files = [to_dict(ti.file_at(i)) for i in range(ti.num_files())]
                torrent_info["files"] = files
                
                video_file = get_largest_video_file(files)
                if video_file:
                    video_file_index = files.index(video_file)
                    
                    # Set priorities: 1 for the video file, 0 for others
                    priorities = [0] * len(files)
                    priorities[video_file_index] = 1
                    handle.prioritize_files(priorities)
                    
                    # Set file deadlines for the warm cache
                    piece_size = ti.piece_length()
                    warm_cache_bytes = WARM_CACHE_SIZE_MB * 1024 * 1024
                    
                    start_piece, _ = ti.map_file(video_file_index, 0, 1)
                    end_piece, _ = ti.map_file(video_file_index, warm_cache_bytes, 1)
                    
                    for i in range(start_piece, end_piece + 1):
                        handle.set_piece_deadline(i, 1000) # 1 second deadline
                        
                    logging.info(f"Prioritizing warm cache ({WARM_CACHE_SIZE_MB}MB) for {video_file['name']}")
                else:
                    logging.warning(f"No video file found for torrent {torrent_hash}")

            elif isinstance(alert, lt.piece_finished_alert):
                handle = alert.handle
                if not handle.is_valid() or str(handle.info_hash()).lower() not in active_torrents:
                    continue
                
                torrent_hash = str(handle.info_hash()).lower()
                torrent_info = active_torrents[torrent_hash]
                
                if torrent_info["status"] == "downloading_warm_cache":
                    ti = handle.get_torrent_info()
                    files = torrent_info["files"]
                    video_file = get_largest_video_file(files)
                    if video_file:
                        video_file_index = files.index(video_file)
                        warm_cache_bytes = WARM_CACHE_SIZE_MB * 1024 * 1024
                        
                        # Check if warm cache download is complete
                        file_status = handle.file_progress(flags=lt.file_progress_flags_t.piece_granularity)
                        if file_status[video_file_index] * ti.piece_length() >= warm_cache_bytes:
                            logging.info(f"Warm cache download complete for {torrent_hash}. Pausing torrent.")
                            handle.pause()
                            torrent_info["status"] = "paused"
                            # Reset deadlines
                            for i in range(ti.num_pieces()):
                                handle.clear_piece_deadlines(i)

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
                    
                # Pause the torrent if it's not already
                handle = torrent_info["handle"]
                if not handle.status().paused:
                    handle.pause()
                    torrent_info["status"] = "paused"
                    logging.info(f"Paused torrent {torrent_id} after HLS cleanup.")

def to_dict(file_entry):
    return {
        'path': file_entry.path,
        'size': file_entry.size,
        'offset': file_entry.offset
    }
