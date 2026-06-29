
import asyncio
import json
import logging
import os
import signal
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
INITIAL_BUFFER_BYTES = 128 * 1024 * 1024
SEEK_WINDOW_BYTES = 24 * 1024 * 1024
APPROX_BYTES_PER_SECOND = 2_000_000
SEEK_BUFFER_BYTES = 128 * 1024 * 1024
PROBE_BUFFER_BYTES = 16 * 1024 * 1024
INITIAL_BUFFER_WAIT_SECONDS = 60
PLAYLIST_WAIT_SECONDS = 30
SEGMENT_WAIT_SECONDS = 30
FFMPEG_PACE_CHECK_SECONDS = 0.25
FFMPEG_MIN_AHEAD_BYTES = 128 * 1024 * 1024
FFMPEG_RESUME_AHEAD_BYTES = 256 * 1024 * 1024
FFMPEG_REPRIO_STEP_BYTES = 8 * 1024 * 1024


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


def _are_pieces_available(handle, start_piece, end_piece):
    try:
        pieces = getattr(handle.status(), "pieces", None)
    except RuntimeError:
        return False

    if pieces is not None:
        if start_piece < 0 or end_piece >= len(pieces):
            return False

        return all(pieces[piece] for piece in range(start_piece, end_piece + 1))

    try:
        return all(handle.have_piece(piece) for piece in range(start_piece, end_piece + 1))
    except (AttributeError, RuntimeError):
        return False


def _is_torrent_complete(torrent_info):
    handle = torrent_info["handle"]
    try:
        status = handle.status()
    except RuntimeError:
        return False

    is_seeding = getattr(status, "is_seeding", False)
    if callable(is_seeding):
        is_seeding = is_seeding()

    return bool(is_seeding) or status.progress >= 0.9999 or torrent_info.get("status") == "completed"


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

    current_priorities = list(handle.get_piece_priorities())
    priorities = current_priorities[:]
    changed = False

    file_end_piece = ti.map_file(file_index, file_entry.size - 1, 1).piece
    for piece in range(file_start_piece, file_end_piece + 1):
        if piece < current_piece:
            priority = 1
        elif piece <= window_end_piece:
            priority = 7
        else:
            priority = 4

        if piece < len(priorities) and priorities[piece] != priority:
            priorities[piece] = priority
            changed = True

    if changed:
        handle.prioritize_pieces(priorities)


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


def _is_byte_range_available(torrent_info, file_index, byte_offset, window_bytes):
    if _is_torrent_complete(torrent_info):
        return True

    start_piece, end_piece = _get_piece_window(torrent_info, file_index, byte_offset, window_bytes)
    if start_piece is None or end_piece is None:
        return False

    return _are_pieces_available(torrent_info["handle"], start_piece, end_piece)


async def _wait_for_initial_buffer(torrent_info, file_index, file_size):
    if file_index is None or file_size <= 0:
        return

    await _wait_for_byte_range(torrent_info, file_index, 0, min(file_size, INITIAL_BUFFER_BYTES))


async def _wait_for_byte_range(torrent_info, file_index, byte_offset, window_bytes):
    if file_index is None:
        return

    deadline = asyncio.get_running_loop().time() + INITIAL_BUFFER_WAIT_SECONDS

    while asyncio.get_running_loop().time() < deadline:
        if _is_byte_range_available(torrent_info, file_index, byte_offset, window_bytes):
            return
        await asyncio.sleep(1)

    raise HTTPException(status_code=503, detail="Seek target buffer not ready")


async def _estimate_bytes_per_second(source_file_path, fallback_bytes_per_second):
    ffprobe_cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=bit_rate,duration,size',
        '-of', 'json',
        source_file_path,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *ffprobe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except FileNotFoundError:
        logging.warning("ffprobe not found; using fallback byte-rate estimate")
        return fallback_bytes_per_second

    if process.returncode != 0:
        logging.warning("ffprobe failed (%s): %s", process.returncode, stderr.decode(errors='replace').strip())
        return fallback_bytes_per_second

    try:
        payload = json.loads(stdout.decode(errors='replace'))
    except json.JSONDecodeError:
        return fallback_bytes_per_second

    fmt = payload.get("format", {})
    bit_rate = fmt.get("bit_rate")
    if bit_rate:
        try:
            value = int(float(bit_rate) / 8)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass

    duration = fmt.get("duration")
    size = fmt.get("size")
    try:
        duration_f = float(duration)
        size_i = int(size)
        if duration_f > 0 and size_i > 0:
            return int(size_i / duration_f)
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    return fallback_bytes_per_second


async def _terminate_hls_process(torrent_info):
    pacer_task = torrent_info.get("hls_pacer_task")
    if pacer_task and not pacer_task.done():
        pacer_task.cancel()
    torrent_info["hls_pacer_task"] = None

    process = torrent_info.get("hls_process")
    if not process or process.returncode is not None:
        torrent_info["hls_process"] = None
        torrent_info["hls_process_paused"] = False
        return

    if torrent_info.get("hls_process_paused"):
        try:
            os.kill(process.pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        torrent_info["hls_process_paused"] = False

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    finally:
        torrent_info["hls_process"] = None
        torrent_info["hls_process_paused"] = False


def _clear_hls_output_dir(hls_output_dir):
    os.makedirs(hls_output_dir, exist_ok=True)
    for existing_path in Path(hls_output_dir).glob("*"):
        if existing_path.is_file():
            existing_path.unlink()


def _build_ffmpeg_cmd(torrent_id, source_file_path, hls_output_dir, playlist_path, start_segment):
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
        '-hls_base_url', f'/api/stream/{torrent_id}/',
        '-start_number', '0',
        '-hls_segment_filename', os.path.join(hls_output_dir, 'segment%03d.ts'),
        playlist_path,
    ])
    return ffmpeg_cmd


async def _log_ffmpeg_stderr(process, torrent_id):
    if not process.stderr:
        return

    while True:
        line = await process.stderr.readline()
        if not line:
            break
        logging.warning(f"[ffmpeg:{torrent_id}] {line.decode(errors='replace').rstrip()}")


def _latest_segment_index(hls_output_dir):
    latest = None
    for segment_path in Path(hls_output_dir).glob("segment*.ts"):
        name = segment_path.stem
        suffix = name.replace("segment", "")
        if suffix.isdigit():
            segment_num = int(suffix)
            if latest is None or segment_num > latest:
                latest = segment_num
    return latest


def _estimated_read_offset_bytes(start_segment, hls_output_dir, bytes_per_second):
    latest_segment = _latest_segment_index(hls_output_dir)
    if latest_segment is None:
        relative_segment_index = 0
    else:
        relative_segment_index = latest_segment + 1

    segment_index = start_segment + relative_segment_index
    return segment_index * HLS_SEGMENT_DURATION_SECONDS * bytes_per_second


def _get_process_file_offset(process, source_file_path):
    fd_dir = Path(f"/proc/{process.pid}/fd")
    fdinfo_dir = Path(f"/proc/{process.pid}/fdinfo")
    if not fd_dir.exists() or not fdinfo_dir.exists():
        return None

    for fd_path in fd_dir.iterdir():
        try:
            target_path = os.readlink(fd_path)
        except OSError:
            continue

        if target_path.endswith(" (deleted)"):
            target_path = target_path[:-10]

        try:
            if not os.path.samefile(target_path, source_file_path):
                continue
        except OSError:
            if os.path.abspath(target_path) != os.path.abspath(source_file_path):
                continue

        fdinfo_path = fdinfo_dir / fd_path.name
        try:
            fdinfo = fdinfo_path.read_text()
        except OSError:
            return None

        for line in fdinfo.splitlines():
            if line.startswith("pos:"):
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError):
                    return None

    return None


def _resume_paused_ffmpeg(torrent_info, process):
    if not torrent_info.get("hls_process_paused"):
        return False

    try:
        os.kill(process.pid, signal.SIGCONT)
    except ProcessLookupError:
        torrent_info["hls_process_paused"] = False
        return False

    torrent_info["hls_process_paused"] = False
    return True


async def _pace_ffmpeg_process(torrent_id, torrent_info, process, file_index, hls_output_dir, source_file_path, start_segment):
    last_reprioritized_offset = -1
    tick = 0
    while process.returncode is None:
        tick += 1
        if _is_torrent_complete(torrent_info):
            if _resume_paused_ffmpeg(torrent_info, process):
                logging.info("Resumed FFmpeg for %s because torrent is complete", torrent_id)
            logging.info("Stopping FFmpeg pacing for %s because torrent is complete", torrent_id)
            return

        bytes_per_second = torrent_info.get("stream_bytes_per_second", APPROX_BYTES_PER_SECOND)
        read_offset = _get_process_file_offset(process, source_file_path)
        if read_offset is None:
            read_offset = _estimated_read_offset_bytes(start_segment, hls_output_dir, bytes_per_second)

        if abs(read_offset - last_reprioritized_offset) >= FFMPEG_REPRIO_STEP_BYTES:
            _reprioritize_for_offset(torrent_info, file_index, read_offset)
            last_reprioritized_offset = read_offset

        is_paused = torrent_info.get("hls_process_paused", False)
        min_window_available = _is_byte_range_available(
            torrent_info,
            file_index,
            read_offset,
            FFMPEG_MIN_AHEAD_BYTES,
        )
        resume_window_available = _is_byte_range_available(
            torrent_info,
            file_index,
            read_offset,
            FFMPEG_RESUME_AHEAD_BYTES,
        ) if is_paused else min_window_available

        if not is_paused and not min_window_available:
            try:
                os.kill(process.pid, signal.SIGSTOP)
                torrent_info["hls_process_paused"] = True
                logging.info(
                    "Paused FFmpeg for %s (read=%.1f MiB, next %.0f MiB unavailable)",
                    torrent_id,
                    read_offset / (1024 * 1024),
                    FFMPEG_MIN_AHEAD_BYTES / (1024 * 1024),
                )
            except ProcessLookupError:
                break
        elif is_paused and resume_window_available:
            try:
                os.kill(process.pid, signal.SIGCONT)
                torrent_info["hls_process_paused"] = False
                logging.info(
                    "Resumed FFmpeg for %s (read=%.1f MiB, next %.0f MiB available)",
                    torrent_id,
                    read_offset / (1024 * 1024),
                    FFMPEG_RESUME_AHEAD_BYTES / (1024 * 1024),
                )
            except ProcessLookupError:
                break

        if tick % 20 == 0:
            logging.info(
                "[pace:%s] bps=%.2f MB/s read=%.1f MiB min_window=%s paused=%s",
                torrent_id,
                bytes_per_second / (1024 * 1024),
                read_offset / (1024 * 1024),
                min_window_available,
                torrent_info.get("hls_process_paused", False),
            )

        await asyncio.sleep(FFMPEG_PACE_CHECK_SECONDS)


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

    stream_bytes_per_second = torrent_info.get("stream_bytes_per_second", APPROX_BYTES_PER_SECOND)
    byte_offset = start_segment * HLS_SEGMENT_DURATION_SECONDS * stream_bytes_per_second
    _reprioritize_for_offset(torrent_info, video_file_index, byte_offset)

    await _terminate_hls_process(torrent_info)

    hls_output_dir = os.path.join(HLS_PATH, torrent_id)
    playlist_path = os.path.join(hls_output_dir, "stream.m3u8")
    _clear_hls_output_dir(hls_output_dir)

    source_file_path = os.path.join(DOWNLOAD_PATH, video_file["name"])
    while not os.path.exists(source_file_path):
        logging.info(f"Waiting for source file to exist: {source_file_path}")
        await asyncio.sleep(1)

    _reprioritize_for_offset(torrent_info, video_file_index, 0)
    await _wait_for_byte_range(
        torrent_info,
        video_file_index,
        0,
        min(video_file["size"], PROBE_BUFFER_BYTES),
    )

    stream_bytes_per_second = await _estimate_bytes_per_second(source_file_path, APPROX_BYTES_PER_SECOND)
    torrent_info["stream_bytes_per_second"] = stream_bytes_per_second
    byte_offset = start_segment * HLS_SEGMENT_DURATION_SECONDS * stream_bytes_per_second
    _reprioritize_for_offset(torrent_info, video_file_index, byte_offset)

    if start_segment == 0:
        await _wait_for_initial_buffer(torrent_info, video_file_index, video_file["size"])
    else:
        await _wait_for_byte_range(torrent_info, video_file_index, byte_offset, SEEK_BUFFER_BYTES)

    ffmpeg_cmd = _build_ffmpeg_cmd(torrent_id, source_file_path, hls_output_dir, playlist_path, start_segment)
    logging.info(f"Starting FFmpeg for {torrent_id}: {' '.join(ffmpeg_cmd)}")
    process = await asyncio.create_subprocess_exec(
        *ffmpeg_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    torrent_info["hls_process"] = process
    torrent_info["hls_start_segment"] = start_segment
    torrent_info["hls_process_paused"] = False
    asyncio.create_task(_log_ffmpeg_stderr(process, torrent_id))
    torrent_info["hls_pacer_task"] = asyncio.create_task(
        _pace_ffmpeg_process(
            torrent_id,
            torrent_info,
            process,
            video_file_index,
            hls_output_dir,
            source_file_path,
            start_segment,
        )
    )

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
    return {"ok": True, "seek_offset_seconds": segment * HLS_SEGMENT_DURATION_SECONDS}

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
