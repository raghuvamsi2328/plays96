import libtorrent as lt

def get_torrent_status(torrent_info):
    """
    Converts a torrent info dictionary to a detailed status dictionary.
    """
    handle = torrent_info["handle"]
    if not handle.is_valid():
        # Return a minimal status if handle is not valid yet
        return {
            "hash": torrent_info.get("hash", "N/A"),
            "name": "Connecting...",
            "status": torrent_info.get("status", "metadata"),
            "progress": 0,
            "download_rate": 0,
            "upload_rate": 0,
            "num_peers": 0,
            "files": [],
        }

    s = handle.status()
    info = handle.get_torrent_info()
    
    files = []
    if info and info.num_files() > 0:
        files = [to_dict(info.file_at(i)) for i in range(info.num_files())]
    # If info isn't ready, files will be an empty list, which is the correct state.

    status_str = torrent_info.get("status", str(s.state))
    progress = s.progress * 100
    if status_str == "metadata":
        progress = 0.0

    return {
        "hash": str(s.info_hashes.v1).lower() if s.info_hashes.v1 else str(s.info_hashes.v2).lower(),
        "name": info.name() if info else "N/A",
        "status": status_str,
        "progress": progress,
        "download_rate": s.download_rate / 1000,  # KB/s
        "upload_rate": s.upload_rate / 1000,    # KB/s
        "num_peers": s.num_peers,
        "files": files,
    }

def to_dict(file_entry):
    """
    Converts a libtorrent file_entry to a dictionary that matches our Pydantic model.
    """
    video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv']
    is_video = any(file_entry.path.lower().endswith(ext) for ext in video_extensions)
    
    return {
        'name': file_entry.path,
        'size': file_entry.size,
        'progress': 0.0,  # Default progress to 0, it will be updated later
        'is_video': is_video
    }

def get_largest_video_file(files):
    """Finds the largest video file in a list of files."""
    best_file = None
    max_size = -1
    for file in files:
        if file.get("is_video") and file.get("size") > max_size:
            max_size = file["size"]
            best_file = file
    return best_file
