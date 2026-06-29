
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import libtorrent as lt
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from src.config import DOWNLOAD_PATH, HLS_PATH
from src.state import active_torrents
from src.utils import get_largest_video_file

router = APIRouter()

HLS_SEGMENT_DURATION_SECONDS = 10
INITIAL_BUFFER_BYTES = 32 * 1024 * 1024
SEEK_WINDOW_BYTES = 64 * 1024 * 1024
APPROX_BYTES_PER_SECOND = 2_000_000
SEEK_BUFFER_BYTES = 8 * 1024 * 1024
INITIAL_BUFFER_WAIT_SECONDS = 60
PLAYLIST_WAIT_SECONDS = 30
SEGMENT_WAIT_SECONDS = 30


def _get_video_file_and_index(torrent_info):
    video_file = get_largest_video_file(torrent_info.get("files", []))
    if not video_file:
        return None, None

    for file_info in torrent_info.get("files", []):
        if file_info.get("name") == video_file.get("name"):
            return video_file, file_info.get("index")

    return video_file, None


def _get_torrent_lock(torrent_info):
    lock = torrent_info.get("hls_lock")
    if lock is None:
        lock = asyncio.Lock()
        torrent_info["hls_lock"] = lock
    return lock


def _get_file_progress_bytes(handle, file_index):
    try:
        progress = handle.file_progress(flags=1)
    except TypeError:
        progress = handle.file_progress()
    except RuntimeError:
        return None

    if file_index is None or file_index < 0 or file_index >= len(progress):
        return None

    return progress[file_index]


def _are_pieces_available(handle, start_piece, end_piece):
    try:
        pieces = getattr(handle.status(), "pieces", None)
    except RuntimeError:
        return False

    if not pieces:
        return False

    if start_piece < 0 or end_piece >= len(pieces):
        return False

    return all(pieces[piece] for piece in range(start_piece, end_piece + 1))


def _reprioritize_for_offset(torrent_info, file_index, byte_offset):
    if file_index is None:
        return

    handle = torrent_info["handle"]
    ti = handle.get_torrent_info()
    file_entry = ti.file_at(file_index)
    if file_entry.size <= 0:
        return

    file_start_piece = ti.map_file(file_index, 0, 1).piece
    current_piece = ti.map_file(file_index, min(byte_offset, file_entry.size - 1), 1).piece
    window_end_piece = ti.map_file(
        file_index,
        min(file_entry.size - 1, byte_offset + SEEK_WINDOW_BYTES),
        1,
    ).piece

    file_end_piece = ti.map_file(file_index, file_entry.size - 1, 1).piece
    for piece in range(file_start_piece, file_end_piece + 1):
        if piece < current_piece:
            priority = 1
        elif piece <= window_end_piece:
            priority = 7
        else:
            priority = 4
        handle.piece_priority(piece, priority)


def _get_piece_window(torrent_info, file_index, byte_offset, window_bytes):
    handle = torrent_info["handle"]
    ti = handle.get_torrent_info()
    file_entry = ti.file_at(file_index)
    if file_entry.size <= 0:
        return None, None

    start_offset = max(0, min(byte_offset, file_entry.size - 1))
    end_offset = max(start_offset, min(file_entry.size - 1, start_offset + window_bytes))

    start_piece = ti.map_file(file_index, start_offset, 1).piece
    end_piece = ti.map_file(file_index, end_offset, 1).piece
    return start_piece, end_piece


async def _wait_for_initial_buffer(torrent_info, file_index, file_size):
    if file_index is None or file_size <= 0:
        return

    target_bytes = min(file_size, INITIAL_BUFFER_BYTES)
    handle = torrent_info["handle"]
    deadline = asyncio.get_running_loop().time() + INITIAL_BUFFER_WAIT_SECONDS

    while asyncio.get_running_loop().time() < deadline:
        downloaded_bytes = _get_file_progress_bytes(handle, file_index)
        if downloaded_bytes is None:
            logging.warning("File progress is unavailable; starting FFmpeg without buffered-bytes check")
            return
        if downloaded_bytes is not None and downloaded_bytes >= target_bytes:
            return
        await asyncio.sleep(1)

    raise HTTPException(status_code=503, detail="Initial stream buffer not ready")


async def _wait_for_byte_range(torrent_info, file_index, byte_offset, window_bytes):
    if file_index is None:
        return

    handle = torrent_info["handle"]
    deadline = asyncio.get_running_loop().time() + INITIAL_BUFFER_WAIT_SECONDS
    start_piece, end_piece = _get_piece_window(torrent_info, file_index, byte_offset, window_bytes)

    while asyncio.get_running_loop().time() < deadline:
        if start_piece is not None and end_piece is not None and _are_pieces_available(handle, start_piece, end_piece):
            return

        downloaded_bytes = _get_file_progress_bytes(handle, file_index)
        if downloaded_bytes is None:
            logging.warning("Piece availability is unavailable; continuing seek without byte-range confirmation")
            return
        if downloaded_bytes >= byte_offset + min(window_bytes, SEEK_BUFFER_BYTES):
            return
        await asyncio.sleep(1)

    raise HTTPException(status_code=503, detail="Seek target buffer not ready")


async def _terminate_hls_process(torrent_info):
    process = torrent_info.get("hls_process")
    if not process or process.returncode is not None:
        torrent_info["hls_process"] = None
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    finally:
        torrent_info["hls_process"] = None


def _clear_hls_output_dir(hls_output_dir):
    os.makedirs(hls_output_dir, exist_ok=True)
    for existing_path in Path(hls_output_dir).glob("*"):
        if existing_path.is_file():
            existing_path.unlink()


def _build_ffmpeg_cmd(source_file_path, hls_output_dir, playlist_path, start_segment):
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel', 'warning',
        '-nostdin',
        '-y',
    ]

    if start_segment > 0:
        ffmpeg_cmd.extend([
            '-ss', str(start_segment * HLS_SEGMENT_DURATION_SECONDS),
        ])

    ffmpeg_cmd.extend([
        '-i', source_file_path,
        '-c:a', 'aac',
        '-c:v', 'copy',
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_DURATION_SECONDS),
        '-hls_list_size', '0',
        '-hls_flags', 'independent_segments',
        '-hls_segment_type', 'mpegts',
        '-start_number', str(start_segment),
        '-hls_segment_filename', os.path.join(hls_output_dir, 'segment%03d.ts'),
        playlist_path,
    ])
    return ffmpeg_cmd


async def _start_hls_process(torrent_id, torrent_info, start_segment):
    handle = torrent_info["handle"]
    if handle.status().paused:
        handle.resume()
        torrent_info["status"] = "downloading"

    video_file, video_file_index = _get_video_file_and_index(torrent_info)
    if not video_file:
        raise HTTPException(status_code=404, detail="No video file found in torrent.")

    if video_file_index is None:
        raise HTTPException(status_code=409, detail="Video file index not ready")

    priorities = [1] * len(torrent_info["files"])
    priorities[video_file_index] = 7
    handle.prioritize_files(priorities)

    torrent_info["video_file_index"] = video_file_index

    byte_offset = start_segment * HLS_SEGMENT_DURATION_SECONDS * APPROX_BYTES_PER_SECOND
    _reprioritize_for_offset(torrent_info, video_file_index, byte_offset)

    await _terminate_hls_process(torrent_info)

    hls_output_dir = os.path.join(HLS_PATH, torrent_id)
    playlist_path = os.path.join(hls_output_dir, "stream.m3u8")
    _clear_hls_output_dir(hls_output_dir)

    source_file_path = os.path.join(DOWNLOAD_PATH, video_file["name"])
    while not os.path.exists(source_file_path):
        logging.info(f"Waiting for source file to exist: {source_file_path}")
        await asyncio.sleep(1)

    if start_segment == 0:
        await _wait_for_initial_buffer(torrent_info, video_file_index, video_file["size"])
    else:
        await _wait_for_byte_range(torrent_info, video_file_index, byte_offset, SEEK_BUFFER_BYTES)

    ffmpeg_cmd = _build_ffmpeg_cmd(source_file_path, hls_output_dir, playlist_path, start_segment)
    logging.info(f"Starting FFmpeg for {torrent_id}: {' '.join(ffmpeg_cmd)}")
    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    torrent_info["hls_process"] = process
    torrent_info["hls_start_segment"] = start_segment

    return playlist_path


async def _wait_for_hls_artifact(path, timeout_seconds, process=None, artifact_name="artifact"):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return
        if process and process.returncode is not None:
            raise HTTPException(status_code=500, detail=f"FFmpeg exited before {artifact_name} was ready")
        await asyncio.sleep(1)

    raise HTTPException(status_code=404, detail=f"{artifact_name.capitalize()} not ready")

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
    video_file, video_file_index = _get_video_file_and_index(torrent_info)
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
        video_file, video_file_index = _get_video_file_and_index(torrent_info)
        if not video_file:
            raise HTTPException(status_code=404, detail="No video file found in torrent.")

    playlist_path = os.path.join(HLS_PATH, torrent_id, "stream.m3u8")
    async with _get_torrent_lock(torrent_info):
        if not torrent_info.get("hls_process") or torrent_info["hls_process"].returncode is not None:
            logging.info(f"HLS process not running for {torrent_id}. Starting now.")
            playlist_path = await _start_hls_process(torrent_id, torrent_info, start_segment=0)

    # Wait for the playlist file to be created
    await _wait_for_hls_artifact(
        Path(playlist_path),
        PLAYLIST_WAIT_SECONDS,
        process=torrent_info.get("hls_process"),
        artifact_name="playlist",
    )

    return FileResponse(playlist_path, media_type='application/vnd.apple.mpegurl')


@router.post("/{torrent_id}/seek")
async def notify_seek(torrent_id: str, segment: int = Query(..., ge=0)):
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")

    video_file_index = torrent_info.get("video_file_index")
    if video_file_index is None:
        _, video_file_index = _get_video_file_and_index(torrent_info)

    if video_file_index is None:
        raise HTTPException(status_code=409, detail="Video file not ready")

    async with _get_torrent_lock(torrent_info):
        await _start_hls_process(torrent_id, torrent_info, start_segment=segment)

    torrent_info["hls_last_accessed"] = datetime.now()
    return {"ok": True}

@router.get("/{torrent_id}/{segment}")
async def get_hls_segment(torrent_id: str, segment: str):
    """Serves the individual .ts segment files."""
    torrent_id = torrent_id.lower()
    segment_path = Path(HLS_PATH) / torrent_id / segment
    await _wait_for_hls_artifact(segment_path, SEGMENT_WAIT_SECONDS, artifact_name="segment")
    
    # Update access time on segment access
    if torrent_id in active_torrents:
        active_torrents[torrent_id]["hls_last_accessed"] = datetime.now()
    return FileResponse(str(segment_path), media_type='video/MP2T')
