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
        files = []
        for i in range(ti.num_files()):
            file_entry = ti.file_at(i)
            files.append({
                "index": i,
                "name": file_entry.path,
                "size": file_entry.size,
                "progress": 0.0,
                "is_video": any(file_entry.path.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov'])
            })
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
            
        # Prioritize the video file for download
        if "files" in torrent_info and torrent_info["files"]:
            ti = handle.get_torrent_info()
            video_file_index = None
            for i, file_info in enumerate(torrent_info["files"]):
                if file_info["name"] == video_file["name"]:
                    video_file_index = i
                    break
            
            if video_file_index is not None:
                priorities = [1] * len(torrent_info["files"])  # Normal priority for all
                priorities[video_file_index] = 7  # High priority for video file
                handle.prioritize_files(priorities)
                logging.info(f"Prioritized video file: {video_file['name']}")
            
        os.makedirs(hls_output_dir, exist_ok=True)
        
        source_file_path = os.path.join(DOWNLOAD_PATH, video_file["name"])

        # Wait for the file to exist before starting ffmpeg with timeout
        start_time = datetime.now()
        file_wait_timeout = timedelta(minutes=5)  # 5 minutes timeout for file to appear
        
        while not os.path.exists(source_file_path):
            if datetime.now() - start_time > file_wait_timeout:
                logging.error(f"Timeout waiting for source file: {source_file_path}")
                raise HTTPException(
                    status_code=500,
                    detail="Timeout waiting for video file to be downloaded"
                )
            logging.info(f"Waiting for source file to exist: {source_file_path}")
            await asyncio.sleep(1)

        # Ensure paths with spaces are properly quoted for FFmpeg
        def escape_path(path):
            # Replace backslashes with forward slashes for FFmpeg
            path = path.replace('\\', '/')
            # Quote the path if it contains spaces
            if ' ' in path:
                return f'"{path}"'
            return path

        source_file_path_escaped = escape_path(source_file_path)
        hls_segment_path_escaped = escape_path(os.path.join(hls_output_dir, 'segment%03d.ts'))
        playlist_path_escaped = escape_path(playlist_path)

        ffmpeg_cmd = [
            'ffmpeg',
            '-i', source_file_path_escaped,
            '-c:a', 'aac',
            '-c:v', 'copy',
            '-f', 'hls',
            '-hls_time', '10',
            '-hls_list_size', '0', # Keep all segments
            '-hls_segment_filename', hls_segment_path_escaped,
            playlist_path_escaped
        ]
        
        logging.info(f"Starting FFmpeg for {torrent_id}: {' '.join(ffmpeg_cmd)}")
        process = await asyncio.create_subprocess_exec(*ffmpeg_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        torrent_info["hls_process"] = process

        # Start monitoring the process stderr in background
        async def monitor_stderr():
            stderr = []
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr.append(line.decode().strip())
                logging.debug(f"FFmpeg stderr: {line.decode().strip()}")
            return stderr

        stderr_task = asyncio.create_task(monitor_stderr())

        # Wait for the playlist file to be created with timeout
        start_time = datetime.now()
        timeout = timedelta(minutes=2)  # 2 minutes timeout
        
        while not os.path.exists(playlist_path):
            # Check if process is still running
            if process.returncode is not None:
                stderr_output = await stderr_task
                error_msg = "\n".join(stderr_output) if stderr_output else "Unknown error"
                logging.error(f"FFmpeg process failed with return code {process.returncode}. Error: {error_msg}")
                raise HTTPException(
                    status_code=500,
                    detail=f"FFmpeg conversion failed: {error_msg[:200]}..."  # Truncate long error messages
                )

            # Check timeout
            if datetime.now() - start_time > timeout:
                process.terminate()  # Clean up the process
                await process.wait()  # Wait for termination
                logging.error(f"Timeout waiting for playlist creation for torrent {torrent_id}")
                raise HTTPException(
                    status_code=500,
                    detail="Timeout waiting for HLS conversion to start"
                )

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
