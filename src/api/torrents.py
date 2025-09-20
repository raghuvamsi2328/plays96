import asyncio
import logging
import os
import shutil
from datetime import datetime
from typing import List

import libtorrent as lt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import DOWNLOAD_PATH, HLS_PATH
from src.state import active_torrents, get_session
from src.utils import get_torrent_status

router = APIRouter()

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
        params.save_path = DOWNLOAD_PATH
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        handle = ses.add_torrent(params)
        # Wait for the torrent to get info_hash
        while not handle.is_valid() or not handle.has_metadata():
            await asyncio.sleep(0.1)
        torrent_hash = str(handle.info_hash()).lower()

        if torrent_hash in active_torrents:
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
        logging.error(f"Failed to add torrent: {e}")
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
    ses.remove_torrent(handle, lt.session.delete_files)
    
    del active_torrents[torrent_id]

    # Clean up HLS files if they exist
    hls_output_dir = os.path.join(HLS_PATH, torrent_id)
    if os.path.exists(hls_output_dir):
        shutil.rmtree(hls_output_dir)
        
    return {"message": "Torrent removed successfully"}
