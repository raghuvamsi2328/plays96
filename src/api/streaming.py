
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
MIN_STARTUP_BUFFER_BYTES = 8 * 1024 * 1024
MAX_STARTUP_BUFFER_BYTES = 32 * 1024 * 1024
STARTUP_BUFFER_SEGMENTS = 2
SEEK_WINDOW_BYTES = 64 * 1024 * 1024
APPROX_BYTES_PER_SECOND = 2_000_000
SEEK_BUFFER_BYTES = 32 * 1024 * 1024
PROBE_BUFFER_BYTES = 4 * 1024 * 1024
INITIAL_BUFFER_WAIT_SECONDS = 60
PLAYLIST_WAIT_SECONDS = 30
SEGMENT_WAIT_SECONDS = 30
FFMPEG_PACE_CHECK_SECONDS = 0.25
FFMPEG_MIN_AHEAD_BYTES = 16 * 1024 * 1024
FFMPEG_RESUME_AHEAD_BYTES = 32 * 1024 * 1024
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

    try:
        piece_count = len(pieces) if pieces is not None else 0
    except TypeError:
        piece_count = 0

    if piece_count > 0:
        if start_piece < 0 or end_piece >= piece_count:
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


def _get_startup_buffer_bytes(file_size, bytes_per_second):
    target_bytes = bytes_per_second * HLS_SEGMENT_DURATION_SECONDS * STARTUP_BUFFER_SEGMENTS
    target_bytes = max(MIN_STARTUP_BUFFER_BYTES, target_bytes)
    target_bytes = min(MAX_STARTUP_BUFFER_BYTES, target_bytes)
    return min(file_size, int(target_bytes))


def _get_piece_window_status(torrent_info, file_index, byte_offset, window_bytes):
    start_piece, end_piece = _get_piece_window(torrent_info, file_index, byte_offset, window_bytes)
    if start_piece is None or end_piece is None:
        return None, None, 0, 0

    handle = torrent_info["handle"]
    total = end_piece - start_piece + 1
    try:
        pieces = getattr(handle.status(), "pieces", None)
    except RuntimeError:
        return start_piece, end_piece, 0, total

    available = 0
    for piece in range(start_piece, end_piece + 1):
        try:
            piece_count = len(pieces) if pieces is not None else 0
            if piece_count > 0:
                has_piece = piece < piece_count and pieces[piece]
            else:
                has_piece = handle.have_piece(piece)
        except (AttributeError, RuntimeError, TypeError):
            has_piece = False

        if has_piece:
            available += 1

    return start_piece, end_piece, available, total


def _request_piece_window(torrent_info, file_index, byte_offset, window_bytes):
    start_piece, end_piece = _get_piece_window(torrent_info, file_index, byte_offset, window_bytes)
    if start_piece is None or end_piece is None:
        return

    handle = torrent_info["handle"]
    _reprioritize_for_offset(torrent_info, file_index, byte_offset)
    for piece in range(start_piece, end_piece + 1):
        try:
            handle.set_piece_deadline(piece, (piece - start_piece) * 250)
        except (AttributeError, RuntimeError, TypeError):
            return


def _enable_streaming_download_mode(handle):
    try:
        handle.set_sequential_download(True)
    except (AttributeError, RuntimeError, TypeError):
        pass


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

    bytes_per_second = torrent_info.get("stream_bytes_per_second", APPROX_BYTES_PER_SECOND)
    await _wait_for_byte_range(
        torrent_info,
        file_index,
        0,
        _get_startup_buffer_bytes(file_size, bytes_per_second),
        wait_name="HLS startup buffer",
    )


async def _wait_for_byte_range(
    torrent_info,
    file_index,
    byte_offset,
    window_bytes,
    wait_name="HLS byte range",
    timeout_seconds=INITIAL_BUFFER_WAIT_SECONDS,
):
    if file_index is None:
        return

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    next_log_time = 0
    _request_piece_window(torrent_info, file_index, byte_offset, window_bytes)

    while loop.time() < deadline:
        if _is_byte_range_available(torrent_info, file_index, byte_offset, window_bytes):
            return
        if loop.time() >= next_log_time:
            start_piece, end_piece, available, total = _get_piece_window_status(
                torrent_info,
                file_index,
                byte_offset,
                window_bytes,
            )
            logging.info(
                "Waiting for %s at %.1f MiB: pieces %s/%s (%s-%s)",
                wait_name,
                byte_offset / (1024 * 1024),
                available,
                total,
                start_piece,
                end_piece,
            )
            _request_piece_window(torrent_info, file_index, byte_offset, window_bytes)
            next_log_time = loop.time() + 5
        await asyncio.sleep(1)

    raise HTTPException(status_code=503, detail=f"{wait_name} not ready")


def _playlist_exists(playlist_path):
    path = Path(playlist_path)
    return path.exists() and path.stat().st_size > 0


def _can_reuse_hls_playlist(torrent_info, playlist_path, start_segment):
    if not _playlist_exists(playlist_path):
        return False

    if torrent_info.get("hls_start_segment", 0) != start_segment:
        return False

    process = torrent_info.get("hls_process")
    if process and process.returncode is None:
        return True

    return _is_torrent_complete(torrent_info)


async def _probe_media_info(source_file_path, fallback_bytes_per_second):
    ffprobe_cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=bit_rate,duration,size:stream=codec_type,codec_name',
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
        return {"bytes_per_second": fallback_bytes_per_second, "duration_seconds": None, "video_codec": None}

    if process.returncode != 0:
        logging.warning("ffprobe failed (%s): %s", process.returncode, stderr.decode(errors='replace').strip())
        return {"bytes_per_second": fallback_bytes_per_second, "duration_seconds": None, "video_codec": None}

    try:
        payload = json.loads(stdout.decode(errors='replace'))
    except json.JSONDecodeError:
        return {"bytes_per_second": fallback_bytes_per_second, "duration_seconds": None, "video_codec": None}

    video_codec = None
    for stream in payload.get("streams", []):
        if stream.get("codec_type") == "video":
            video_codec = stream.get("codec_name")
            break

    fmt = payload.get("format", {})
    duration = fmt.get("duration")
    size = fmt.get("size")
    duration_seconds = None
    size_i = None
    try:
        duration_f = float(duration)
        if duration_f > 0:
            duration_seconds = duration_f
    except (TypeError, ValueError):
        pass

    try:
        size_i = int(size)
    except (TypeError, ValueError):
        pass

    bit_rate = fmt.get("bit_rate")
    if bit_rate:
        try:
            value = int(float(bit_rate) / 8)
            if value > 0:
                return {
                    "bytes_per_second": value,
                    "duration_seconds": duration_seconds,
                    "video_codec": video_codec,
                }
        except (TypeError, ValueError):
            pass

    if duration_seconds and size_i and size_i > 0:
        return {
            "bytes_per_second": int(size_i / duration_seconds),
            "duration_seconds": duration_seconds,
            "video_codec": video_codec,
        }

    return {
        "bytes_per_second": fallback_bytes_per_second,
        "duration_seconds": duration_seconds,
        "video_codec": video_codec,
    }


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


def _build_ffmpeg_cmd(torrent_id, source_file_path, hls_output_dir, playlist_path, start_segment, video_codec=None):
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
    ])

    if video_codec in {"hevc", "h265"}:
        ffmpeg_cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '23',
        ])
    else:
        ffmpeg_cmd.extend([
            '-c:v', 'copy',
        ])

    ffmpeg_cmd.extend([
        '-f', 'hls',
        '-hls_time', str(HLS_SEGMENT_DURATION_SECONDS),
        '-hls_list_size', '0',
        '-hls_playlist_type', 'vod',
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
    _enable_streaming_download_mode(handle)

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
        wait_name="HLS probe buffer",
    )

    media_info = await _probe_media_info(source_file_path, APPROX_BYTES_PER_SECOND)
    stream_bytes_per_second = media_info["bytes_per_second"]
    torrent_info["stream_bytes_per_second"] = stream_bytes_per_second
    torrent_info["stream_duration_seconds"] = media_info["duration_seconds"]
    torrent_info["stream_video_codec"] = media_info["video_codec"]
    byte_offset = start_segment * HLS_SEGMENT_DURATION_SECONDS * stream_bytes_per_second
    _reprioritize_for_offset(torrent_info, video_file_index, byte_offset)

    if start_segment == 0:
        await _wait_for_initial_buffer(torrent_info, video_file_index, video_file["size"])
    else:
        await _wait_for_byte_range(
            torrent_info,
            video_file_index,
            byte_offset,
            SEEK_BUFFER_BYTES,
            wait_name="HLS seek buffer",
        )

    ffmpeg_cmd = _build_ffmpeg_cmd(
        torrent_id,
        source_file_path,
        hls_output_dir,
        playlist_path,
        start_segment,
        video_codec=torrent_info.get("stream_video_codec"),
    )
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
        if _can_reuse_hls_playlist(torrent_info, playlist_path, start_segment=0):
            logging.debug("Reusing existing HLS playlist for %s", torrent_id)
        else:
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


@router.get("/{torrent_id}/metadata")
async def get_stream_metadata(torrent_id: str):
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")

    video_file, video_file_index = _get_video_file_and_index(torrent_info)
    if not video_file:
        raise HTTPException(status_code=404, detail="No video file found in torrent.")

    if video_file_index is not None:
        torrent_info["video_file_index"] = video_file_index

    source_file_path = os.path.join(DOWNLOAD_PATH, video_file["name"])
    if (
        torrent_info.get("stream_duration_seconds") is None
        or torrent_info.get("stream_video_codec") is None
        or torrent_info.get("stream_bytes_per_second") is None
    ) and os.path.exists(source_file_path):
        media_info = await _probe_media_info(source_file_path, APPROX_BYTES_PER_SECOND)
        torrent_info["stream_bytes_per_second"] = media_info["bytes_per_second"]
        torrent_info["stream_duration_seconds"] = media_info["duration_seconds"]
        torrent_info["stream_video_codec"] = media_info["video_codec"]

    return {
        "torrent_id": torrent_id,
        "file_name": video_file.get("name"),
        "file_size": video_file.get("size"),
        "duration_seconds": torrent_info.get("stream_duration_seconds"),
        "video_codec": torrent_info.get("stream_video_codec"),
        "bytes_per_second": torrent_info.get("stream_bytes_per_second"),
    }

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
