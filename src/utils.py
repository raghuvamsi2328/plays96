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
    
    # Use the files list from torrent_info if available, otherwise build it
    files = torrent_info.get("files", [])
    if not files and info and info.num_files() > 0:
         files = [to_dict(info.file_at(i)) for i in range(info.num_files())]


    return {
        "hash": str(s.info_hashes.v1).lower() if s.info_hashes.v1 else str(s.info_hashes.v2).lower(),
        "name": info.name() if info else "N/A",
        "status": torrent_info.get("status", str(s.state)),
        "progress": s.progress * 100,
        "download_rate": s.download_rate / 1000,  # KB/s
        "upload_rate": s.upload_rate / 1000,    # KB/s
        "num_peers": s.num_peers,
        "files": files,
    }

def to_dict(file_entry):
    """
    Converts a libtorrent file_entry to a dictionary that matches our Pydantic model.
    """
    return {
        'name': file_entry.path,
        'size': file_entry.size,
        'progress': 0.0 # Default progress to 0, it will be updated later
    }

def get_largest_video_file(files):
    """Finds the largest video file in a list of files."""
    best_file = None
    max_size = -1
    for file in files:
        if file.get("isVideo") and file.get("size") > max_size:
            max_size = file["size"]
            best_file = file
    return best_file
