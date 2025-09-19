import libtorrent as lt

def get_torrent_status(handle):
    """Translates libtorrent status enum to a human-readable string."""
    if not handle.is_valid():
        return "invalid"
    s = handle.status()
    state_str = [
        'queued', 'checking', 'downloading_metadata', 'downloading',
        'finished', 'seeding', 'allocating', 'checking_resume_data'
    ]
    return state_str[s.state]

def to_dict(torrent_info):
    """Creates a serializable dictionary from torrent data for API responses."""
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

def get_largest_video_file(files):
    """Finds the largest video file in a list of files."""
    best_file = None
    max_size = -1
    for file in files:
        if file.get("isVideo") and file.get("size") > max_size:
            max_size = file["size"]
            best_file = file
    return best_file
