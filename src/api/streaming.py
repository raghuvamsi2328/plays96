
import asyncio
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import libtorrent as lt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from src.config import DOWNLOAD_PATH, HLS_PATH, WARM_CACHE_TIMEOUT_MINUTES
from src.state import active_torrents, get_session
from src.utils import get_largest_video_file

router = APIRouter()

logger = logging.getLogger(__name__)

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


    # Use absolute paths with Path for better path handling
    download_base = Path(DOWNLOAD_PATH).resolve()
    hls_base = Path(HLS_PATH).resolve()
    hls_output_dir = hls_base / torrent_id
    playlist_path = hls_output_dir / "stream.m3u8"


    # If HLS process is not running, start it
    if not torrent_info.get("hls_process") or torrent_info["hls_process"].returncode is not None:
        logger.info(f"HLS process not running for {torrent_id}. Starting now.")
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
                priorities = [1] * len(torrent_info["files"])
                priorities[video_file_index] = 7
                handle.prioritize_files(priorities)
                logger.info(f"Prioritized video file: {video_file['name']}")

        # Create HLS output directory with proper permissions
        hls_output_dir.mkdir(parents=True, exist_ok=True, mode=0o777)

        # Use absolute path for source file
        source_file_path = (download_base / video_file["name"]).resolve()
        hls_segment_path = (hls_output_dir / 'segment%03d.ts').resolve()
        playlist_path_abs = playlist_path.resolve()

        logger.info(f"Looking for source file at: {source_file_path}")
        logger.info(f"File exists: {source_file_path.exists()}")
        logger.info(f"Expected file name: {source_file_path.name}")
        logger.info(f"Files in directory: {list(source_file_path.parent.iterdir())}")

        # Wait for the file to exist before starting ffmpeg with timeout
        start_time = datetime.now()
        file_wait_timeout = timedelta(minutes=5)
        while not source_file_path.exists():
            if datetime.now() - start_time > file_wait_timeout:
                logger.error(f"Timeout waiting for source file: {source_file_path}")
                # List what files actually exist for debugging
                if download_base.exists():
                    logger.error(f"Files in download directory: {list(download_base.rglob('*'))[:10]}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Timeout waiting for video file: {video_file['name']}"
                )
            logger.info(f"Waiting for source file to exist: {source_file_path}")
            await asyncio.sleep(1)

        logger.info(f"Source file found: {source_file_path}")
        logger.info(f"File size: {source_file_path.stat().st_size / (1024**3):.2f} GB")

        # FFmpeg command with absolute paths
        ffmpeg_cmd = [
            'ffmpeg',
            '-i', str(source_file_path),
            '-c:a', 'aac',
            '-c:v', 'copy',
            '-f', 'hls',
            '-hls_time', '10',
            '-hls_list_size', '0',
            '-hls_segment_filename', str(hls_segment_path),
            str(playlist_path_abs)
        ]


    # Log the FFmpeg command with all arguments quoted for clarity
    quoted_cmd = ' '.join([f'"{arg}"' if ' ' in arg else arg for arg in ffmpeg_cmd])
    logger.info(f"Starting FFmpeg for {torrent_id}")
    logger.info(f"FFmpeg command: {quoted_cmd}")
    logger.info(f"FFmpeg input file exists (pre-run): {source_file_path.exists()}")

        process = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            cwd='/usr/src/app',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        torrent_info["hls_process"] = process

        # Start monitoring the process stderr in background
        async def monitor_stderr():
            stderr = []
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                stderr.append(line_str)
                logger.debug(f"FFmpeg: {line_str}")
            return stderr

        stderr_task = asyncio.create_task(monitor_stderr())

        # Wait for the playlist file to be created with timeout
        start_time = datetime.now()
        timeout = timedelta(minutes=2)
        while not playlist_path_abs.exists():
            if process.returncode is not None:
                stderr_output = await stderr_task
                error_msg = "\n".join(stderr_output[-20:]) if stderr_output else "Unknown error"
                logger.error(f"FFmpeg process failed with return code {process.returncode}")
                logger.error(f"Error: {error_msg}")
                raise HTTPException(
                    status_code=500,
                    detail=f"FFmpeg conversion failed. Check logs for details."
                )
            if datetime.now() - start_time > timeout:
                process.terminate()
                await process.wait()
                logger.error(f"Timeout waiting for playlist creation for torrent {torrent_id}")
                raise HTTPException(
                    status_code=500,
                    detail="Timeout waiting for HLS conversion to start"
                )
            logger.info(f"Waiting for playlist to be created: {playlist_path_abs}")
            await asyncio.sleep(1)

        logger.info(f"Playlist created successfully: {playlist_path_abs}")
        return FileResponse(str(playlist_path_abs), media_type='application/vnd.apple.mpegurl')


@router.get("/{torrent_id}/{segment}")
async def get_hls_segment(torrent_id: str, segment: str):
    """Serves the individual .ts segment files."""
    torrent_id = torrent_id.lower()
    # Use Path for better path handling
    segment_path = Path(HLS_PATH) / torrent_id / segment
    if not segment_path.exists():
        logger.warning(f"Segment not found: {segment_path.absolute()}")
        raise HTTPException(status_code=404, detail="Segment not found")
    # Update access time on segment access
    if torrent_id in active_torrents:
        active_torrents[torrent_id]["hls_last_accessed"] = datetime.now()
    return FileResponse(str(segment_path), media_type='video/MP2T')
