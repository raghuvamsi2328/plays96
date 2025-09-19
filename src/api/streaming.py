import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta

import libtorrent as lt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from src.config import DOWNLOAD_PATH, HLS_PATH, WARM_CACHE_TIMEOUT_MINUTES
from src.state import active_torrents, get_session
from src.utils import get_largest_video_file

router = APIRouter()

@router.get("/{torrent_id}")
async def get_hls_playlist(torrent_id: str, request: Request):
    """
    This is the main streaming endpoint.
    It triggers the HLS conversion and returns the m3u8 playlist.
    """
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")

    # Update access time
    torrent_info["hls_last_accessed"] = datetime.now()

    # Find the main video file
    video_file = get_largest_video_file(torrent_info.get("files", []))
    if not video_file:
        # If files are not populated yet, wait for metadata
        handle = torrent_info["handle"]
        if not handle.has_metadata():
             raise HTTPException(status_code=503, detail="Metadata not ready, please wait.")
        # Re-fetch files if they were empty before
        ti = handle.get_torrent_info()
        files = [{"name": ti.file_at(i).path, "size": ti.file_at(i).size} for i in range(ti.num_files())]
        torrent_info["files"] = files
        video_file = get_largest_video_file(files)
        if not video_file:
            raise HTTPException(status_code=404, detail="No video file found in torrent.")

    hls_output_dir = os.path.join(HLS_PATH, torrent_id)
    playlist_path = os.path.join(hls_output_dir, "stream.m3u8")

    # If HLS process is not running, start it
    if not torrent_info.get("hls_process") or torrent_info["hls_process"].returncode is not None:
        logging.info(f"HLS process not running for {torrent_id}. Starting now.")
        
        # Ensure the torrent is fully downloading
        handle = torrent_info["handle"]
        if handle.status().paused:
            handle.resume()
            torrent_info["status"] = "downloading"
            
        os.makedirs(hls_output_dir, exist_ok=True)
        
        source_file_path = os.path.join(DOWNLOAD_PATH, video_file["name"])

        # Wait for the warm cache to be ready before starting ffmpeg
        while not os.path.exists(source_file_path):
            logging.info(f"Waiting for source file to exist: {source_file_path}")
            await asyncio.sleep(1)

        ffmpeg_cmd = [
            'ffmpeg',
            '-i', source_file_path,
            '-c:a', 'aac',
            '-c:v', 'copy',
            '-f', 'hls',
            '-hls_time', '10',
            '-hls_list_size', '0', # Keep all segments
            '-hls_segment_filename', os.path.join(hls_output_dir, 'segment%03d.ts'),
            playlist_path
        ]
        
        logging.info(f"Starting FFmpeg for {torrent_id}: {' '.join(ffmpeg_cmd)}")
        process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        torrent_info["hls_process"] = process

    # Wait for the playlist file to be created
    while not os.path.exists(playlist_path):
        logging.info(f"Waiting for playlist to be created: {playlist_path}")
        await asyncio.sleep(1)

    return FileResponse(playlist_path, media_type='application/vnd.apple.mpegurl')

@router.get("/{torrent_id}/{segment}")
async def get_hls_segment(torrent_id: str, segment: str):
    """Serves the individual .ts segment files."""
    torrent_id = torrent_id.lower()
    segment_path = os.path.join(HLS_PATH, torrent_id, segment)
    if not os.path.exists(segment_path):
        raise HTTPException(status_code=404, detail="Segment not found")
    
    # Update access time on segment access
    if torrent_id in active_torrents:
        active_torrents[torrent_id]["hls_last_accessed"] = datetime.now()
        
    return FileResponse(segment_path, media_type='video/MP2T')
