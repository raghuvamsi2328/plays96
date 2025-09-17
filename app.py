import os
import asyncio
import time
import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
import uvicorn
import libtorrent as lt
from pydantic import BaseModel
import subprocess

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
PORT = int(os.getenv("PORT", 6991))
CLEANUP_INTERVAL_HOURS = 12
DOWNLOAD_PATH = "downloads"

# --- FastAPI App Initialization ---
app = FastAPI()

# --- In-memory State ---
active_torrents = {}
# Structure:
# {
#   "torrent_hash": {
#     "handle": lt.torrent_handle,
#     "info": lt.torrent_info,
#     "status": "downloading" | "seeding" | "paused" | "error",
#     "name": str,
#     "files": list,
#     "added_at": datetime,
#     "last_accessed_at": datetime,
#     "error": str | None
#   }
# }

# --- libtorrent Session ---
ses = lt.session({
    'listen_interfaces': f'0.0.0.0:{PORT + 10}',
    'alert_mask': lt.alert.category_t.all_categories,
    'user_agent': 'plays96/1.0.0',
    'download_rate_limit': 0,
    'upload_rate_limit': 0,
    'connections_limit': 200,
    'active_dht_limit': 88,
    'active_tracker_limit': 1600,
    'active_lsd_limit': 60,
    'active_limit': 500,
})
logging.info("libtorrent session started")

# --- Utility Functions ---
def format_bytes(num):
    if num is None:
        return "0 Bytes"
    for unit in ['Bytes', 'KB', 'MB', 'GB', 'TB']:
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"

def get_torrent_status(handle):
    if not handle.is_valid():
        return "invalid"
    s = handle.status()
    state_str = [
        'queued', 'checking', 'downloading_metadata', 'downloading',
        'finished', 'seeding', 'allocating', 'checking_resume_data'
    ]
    return state_str[s.state]

def to_dict(torrent_info):
    """Creates a serializable dictionary from torrent data."""
    handle = torrent_info.get("handle")
    if not handle or not handle.is_valid():
        return {
            "id": torrent_info.get("id"),
            "name": torrent_info.get("name"),
            "status": "error",
            "error": "Invalid handle",
            "progress": 0,
            "downloadSpeed": 0,
            "uploadSpeed": 0,
            "peers": 0,
            "files": [],
        }

    s = handle.status()
    return {
        "id": str(s.info_hash),
        "hash": str(s.info_hash),
        "name": torrent_info.get("name"),
        "status": get_torrent_status(handle),
        "progress": round(s.progress * 100, 2),
        "downloadSpeed": s.download_rate,
        "uploadSpeed": s.upload_rate,
        "peers": s.num_peers,
        "files": torrent_info.get("files", []),
        "addedAt": torrent_info.get("added_at"),
        "lastAccessedAt": torrent_info.get("last_accessed_at"),
        "error": torrent_info.get("error"),
    }

# --- Background Tasks ---
async def alert_listener():
    """Listens for and processes libtorrent alerts."""
    while True:
        alerts = []
        ses.pop_alerts(alerts)
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
                            "isVideo": any(file_entry.path.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov'])
                        })
                    active_torrents[info_hash]["info"] = info
                    active_torrents[info_hash]["name"] = info.name()
                    active_torrents[info_hash]["files"] = files
                    logging.info(f"Metadata received for {info.name()}")

            elif isinstance(alert, lt.torrent_finished_alert):
                h = alert.handle
                logging.info(f"Torrent finished: {h.status().name}")

            elif isinstance(alert, lt.torrent_error_alert):
                h = alert.handle
                info_hash = str(h.info_hash())
                if info_hash in active_torrents:
                    active_torrents[info_hash]["status"] = "error"
                    active_torrents[info_hash]["error"] = alert.error.message()
                logging.error(f"Torrent error for {info_hash}: {alert.error.message()}")

        await asyncio.sleep(1)

async def cleanup_inactive_torrents():
    """Periodically removes torrents that haven't been accessed."""
    while True:
        await asyncio.sleep(3600)  # Check every hour
        now = datetime.now()
        to_remove = []
        for info_hash, torrent in active_torrents.items():
            last_accessed = torrent.get("last_accessed_at", torrent["added_at"])
            if now - last_accessed > timedelta(hours=CLEANUP_INTERVAL_HOURS):
                to_remove.append(info_hash)
                logging.info(f"Marking inactive torrent for cleanup: {torrent['name']}")

        for info_hash in to_remove:
            handle = active_torrents[info_hash].get("handle")
            if handle and handle.is_valid():
                ses.remove_torrent(handle)
            del active_torrents[info_hash]
            logging.info(f"Removed torrent: {info_hash}")

# --- FastAPI Events ---
@app.on_event("startup")
async def startup_event():
    if not os.path.exists(DOWNLOAD_PATH):
        os.makedirs(DOWNLOAD_PATH)
    asyncio.create_task(alert_listener())
    asyncio.create_task(cleanup_inactive_torrents())
    logging.info("Server startup complete. Listeners are running.")

# --- API Models ---
class AddTorrentRequest(BaseModel):
    torrentHash: str
    name: str = None

# --- API Endpoints ---
@app.get("/health")
async def health_check():
    return {
        "status": "OK",
        "timestamp": datetime.now().isoformat(),
        "activeTorrents": len(active_torrents),
    }

@app.get("/torrents")
async def list_torrents():
    return [to_dict(t) for t in active_torrents.values()]

@app.get("/torrent/{torrent_id}")
async def get_torrent(torrent_id: str):
    torrent_info = active_torrents.get(torrent_id.lower())
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return to_dict(torrent_info)

@app.post("/add-torrent")
async def add_torrent(req_body: AddTorrentRequest):
    torrent_hash = req_body.torrentHash.lower()
    if len(torrent_hash) != 40:
        raise HTTPException(status_code=400, detail="Invalid torrent hash format")

    if torrent_hash in active_torrents:
        active_torrents[torrent_hash]["last_accessed_at"] = datetime.now()
        return {"status": "already_active", "torrent": to_dict(active_torrents[torrent_hash])}

    magnet_uri = f"magnet:?xt=urn:btih:{torrent_hash}"
    params = {
        'save_path': DOWNLOAD_PATH,
        'storage_mode': lt.storage_mode_t.storage_mode_sparse,
    }
    handle = ses.add_torrent({'url': magnet_uri, 'params': params})

    torrent_info = {
        "id": torrent_hash,
        "handle": handle,
        "name": req_body.name or f"torrent_{torrent_hash[:8]}",
        "status": get_torrent_status(handle),
        "added_at": datetime.now(),
        "last_accessed_at": datetime.now(),
        "files": [],
        "error": None,
    }
    active_torrents[torrent_hash] = torrent_info
    
    # Wait for metadata
    for _ in range(30): # 30 second timeout
        if torrent_info.get("files"):
            break
        await asyncio.sleep(1)

    return {"status": "adding", "torrent": to_dict(torrent_info)}

@app.get("/stream/{torrent_id}/{file_index}")
async def stream_file(torrent_id: str, file_index: int, request: Request):
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info or not torrent_info.get("info"):
        raise HTTPException(status_code=404, detail="Torrent or metadata not found")

    torrent_info["last_accessed_at"] = datetime.now()
    
    lt_info = torrent_info["info"]
    if file_index < 0 or file_index >= lt_info.num_files():
        raise HTTPException(status_code=404, detail="File index out of bounds")

    file_entry = lt_info.file_at(file_index)
    file_path = os.path.join(DOWNLOAD_PATH, file_entry.path)
    
    # Prioritize the start of the file for streaming
    handle = torrent_info["handle"]
    start_piece, _ = lt_info.map_file(file_index, 0, 1)
    handle.set_piece_deadline(start_piece, 1000) # High priority for the first piece

    # Wait for the file to be created by libtorrent
    for _ in range(15): # Wait up to 15 seconds
        if os.path.exists(file_path):
            break
        await asyncio.sleep(1)
    else:
        raise HTTPException(status_code=503, detail="File not available for streaming yet. Please wait and try again.")

    file_size = file_entry.size
    range_header = request.headers.get('Range')
    
    # --- FFmpeg Remuxing Logic ---
    needs_remux = any(file_entry.path.lower().endswith(ext) for ext in ['.mkv', '.avi', '.wmv', '.flv'])

    if needs_remux:
        logging.info(f"Remuxing required for {file_entry.path}")
        
        async def remux_stream_generator():
            ffmpeg_process = await asyncio.create_subprocess_exec(
                'ffmpeg',
                '-i', file_path,
                '-movflags', 'frag_keyframe+empty_moov',
                '-f', 'mp4',
                '-vcodec', 'copy',
                '-acodec', 'aac',
                '-b:a', '192k',
                'pipe:1',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                while True:
                    chunk = await ffmpeg_process.stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
                    await asyncio.sleep(0.001)
            finally:
                if ffmpeg_process.returncode is None:
                    ffmpeg_process.kill()
                await ffmpeg_process.wait()
                stderr_data = await ffmpeg_process.stderr.read()
                if ffmpeg_process.returncode != 0:
                    logging.error(f"FFmpeg error: {stderr_data.decode()}")

        headers = {
            'Content-Type': 'video/mp4',
            'Accept-Ranges': 'bytes', # Seeking is not perfectly supported with live remuxing
            'Cache-Control': 'no-cache',
        }
        return StreamingResponse(remux_stream_generator(), headers=headers)

    # --- Direct File Streaming Logic ---
    else:
        logging.info(f"Direct streaming for {file_entry.path}")
        
        async def direct_stream_generator(start, end):
            """Generator to read and yield file chunks."""
            # Give libtorrent a head-start on the requested range
            start_piece, _ = lt_info.map_file(file_index, start, 1)
            end_piece, _ = lt_info.map_file(file_index, end, 1)
            handle.set_piece_deadline(start_piece, 1000)
            for p in range(start_piece, end_piece + 1):
                handle.set_piece_deadline(p, 0) # Normal priority for the rest

            with open(file_path, "rb") as f:
                f.seek(start)
                chunk_size = 1024 * 1024  # 1MB chunks
                while (pos := f.tell()) <= end:
                    read_size = min(chunk_size, end - pos + 1)
                    data = f.read(read_size)
                    if not data:
                        # If we're at the end of the file but not the end of the range,
                        # it means the file is still downloading. Wait and retry.
                        if pos < end:
                            await asyncio.sleep(0.5)
                            continue
                        break
                    yield data
                    await asyncio.sleep(0.001) # Yield control

        if range_header:
            start_bytes, end_bytes = range_header.replace('bytes=', '').split('-')
            start = int(start_bytes)
            end = int(end_bytes) if end_bytes else file_size - 1
            headers = {
                'Content-Range': f'bytes {start}-{end}/{file_size}',
                'Accept-Ranges': 'bytes',
                'Content-Length': str(end - start + 1),
                'Content-Type': 'video/mp4',
            }
            return StreamingResponse(direct_stream_generator(start, end), status_code=206, headers=headers)
        else:
            headers = {
                'Content-Length': str(file_size),
                'Content-Type': 'video/mp4',
            }
            return StreamingResponse(direct_stream_generator(0, file_size - 1), headers=headers)

@app.delete("/torrent/{torrent_id}")
async def remove_torrent(torrent_id: str):
    torrent_id = torrent_id.lower()
    if torrent_id in active_torrents:
        handle = active_torrents[torrent_id].get("handle")
        if handle and handle.is_valid():
            ses.remove_torrent(handle, lt.session.delete_files)
        del active_torrents[torrent_id]
        return {"message": "Torrent removed"}
    else:
        raise HTTPException(status_code=404, detail="Torrent not found")

# --- Main Execution ---
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
