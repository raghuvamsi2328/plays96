import asyncio
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List

import libtorrent as lt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import DOWNLOAD_PATH, HLS_PATH
from src.state import active_torrents, get_session
from src.utils import get_torrent_status

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic Models for API requests and responses
class TorrentAddRequest(BaseModel):
    magnet_link: str

class FileStatus(BaseModel):
    name: str
    size: int
    progress: float
    is_video: bool = False

class TorrentStatus(BaseModel):
    hash: str
    name: str
    status: str
    progress: float
    download_rate: float
    upload_rate: float
    num_peers: int
    files: List[FileStatus]


@router.post("", status_code=202, include_in_schema=False)
@router.post("/", status_code=202)
async def add_torrent(request: TorrentAddRequest):
    """
    Adds a new torrent.
    """
    ses = get_session()
    try:
        params = lt.parse_magnet_uri(request.magnet_link)
        
        # CRITICAL FIX: libtorrent expects a string path, not Path object
        # Convert to string to ensure compatibility
        params.save_path = str(DOWNLOAD_PATH)
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        
        logger.info(f"Adding torrent with save_path: {params.save_path}")
        
        handle = ses.add_torrent(params)
        
        # Wait for the torrent to get info_hash with timeout
        max_wait = 30  # 30 seconds timeout
        waited = 0
        while (not handle.is_valid() or not handle.has_metadata()) and waited < max_wait:
            await asyncio.sleep(0.1)
            waited += 0.1
        
        if not handle.is_valid():
            raise Exception("Failed to get valid torrent handle")
            
        torrent_hash = str(handle.info_hash()).lower()
        logger.info(f"Torrent added successfully: {torrent_hash}")

        if torrent_hash in active_torrents:
            logger.info(f"Torrent {torrent_hash} already exists")
            return {"message": "Torrent already exists", "torrent_id": torrent_hash}

        active_torrents[torrent_hash] = {
            "handle": handle,
            "status": "metadata",
            "added_time": datetime.now(),
            "files": [],
            "hls_process": None,
            "hls_last_accessed": None,
        }
        
        return {"message": "Torrent added", "torrent_id": torrent_hash}

    except Exception as e:
        logger.error(f"Failed to add torrent: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[TorrentStatus], include_in_schema=False)
@router.get("/", response_model=List[TorrentStatus])
async def get_all_torrents():
    """Returns the status of all active torrents."""
    statuses = [get_torrent_status(th) for th in active_torrents.values()]
    return statuses


@router.get("/{torrent_id}", response_model=TorrentStatus)
async def get_single_torrent(torrent_id: str):
    """Returns the status of a single torrent."""
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return get_torrent_status(torrent_info)


@router.delete("/{torrent_id}", status_code=200)
async def remove_torrent(torrent_id: str):
    """Removes a torrent and its downloaded files."""
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")

    handle = torrent_info["handle"]
    ses = get_session()
    
    logger.info(f"Removing torrent: {torrent_id}")
    
    try:
        ses.remove_torrent(handle, lt.session.delete_files)
    except Exception as e:
        logger.error(f"Error removing torrent from session: {e}")
    
    del active_torrents[torrent_id]

    # Clean up HLS files if they exist
    hls_output_dir = Path(HLS_PATH) / torrent_id
    if hls_output_dir.exists():
        try:
            shutil.rmtree(hls_output_dir)
            logger.info(f"Cleaned up HLS directory: {hls_output_dir}")
        except Exception as e:
            logger.error(f"Error cleaning up HLS directory: {e}")
        
    return {"message": "Torrent removed successfully"}