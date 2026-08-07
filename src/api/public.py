from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel

from src.api import torrents
from src.state import active_torrents
from src.utils import get_preferred_stream_file, get_torrent_status

router = APIRouter()
compat_router = APIRouter(include_in_schema=False)


class PublicTorrentAddRequest(BaseModel):
    magnet: Optional[str] = None
    magnet_link: Optional[str] = None
    link: Optional[str] = None


def _base_url(request: Request):
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme
        return f"{scheme}://{forwarded_host}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _torrent_or_404(torrent_id):
    torrent_id = torrent_id.lower()
    torrent_info = active_torrents.get(torrent_id)
    if not torrent_info:
        raise HTTPException(status_code=404, detail="Torrent not found")
    return torrent_id, torrent_info


def _links(base_url, torrent_id):
    return {
        "self": f"{base_url}/api/v1/torrents/{torrent_id}",
        "stream": f"{base_url}/api/v1/torrents/{torrent_id}/stream.m3u8",
        "playlist": f"{base_url}/api/v1/torrents/{torrent_id}/playlist.m3u",
        "file_stream": f"{base_url}/api/stream/{torrent_id}?file_index={{file_index}}",
        "file_download": f"{base_url}/api/stream/{torrent_id}/download/{{file_index}}",
        "metadata": f"{base_url}/api/stream/{torrent_id}/metadata",
        "legacy_stream": f"{base_url}/api/stream/{torrent_id}",
    }


def _public_file(file_info, index, default_video, links):
    is_default_stream = bool(default_video and file_info.get("name") == default_video.get("name"))
    stream_url = links["file_stream"].format(file_index=index)
    download_url = links["file_download"].format(file_index=index)
    playlist_url = links["playlist"] + f"?file_index={index}"
    return {
        "index": index,
        "name": file_info.get("name"),
        "size": file_info.get("size", 0),
        "length": file_info.get("size", 0),
        "progress": file_info.get("progress", 0),
        "is_video": file_info.get("is_video", False),
        "is_default_stream": is_default_stream,
        "stream_mode": "hls" if file_info.get("is_video", False) else "direct",
        "stream_url": stream_url,
        "playlist_url": playlist_url,
        "download_url": download_url,
        "link": stream_url,
    }


def _public_torrent(status, request: Request):
    torrent_id = status.get("hash")
    base_url = _base_url(request)
    links = _links(base_url, torrent_id)
    default_video = get_preferred_stream_file(status.get("files", []))
    files = [
        _public_file(file_info, index, default_video, links)
        for index, file_info in enumerate(status.get("files", []))
    ]
    default_index = default_video.get("index") if default_video and default_video.get("index") is not None else 0

    return {
        "id": torrent_id,
        "hash": torrent_id,
        "info_hash": torrent_id,
        "infoHash": torrent_id,
        "name": status.get("name"),
        "status": status.get("status"),
        "progress": status.get("progress", 0),
        "download_rate": status.get("download_rate", 0),
        "upload_rate": status.get("upload_rate", 0),
        "num_peers": status.get("num_peers", 0),
        "files": files,
        "default_file": default_video,
        "stream_url": f"{base_url}/api/stream/{torrent_id}?file_index={default_index}",
        "playlist_url": f"{base_url}/api/v1/torrents/{torrent_id}/playlist.m3u?file_index={default_index}",
        "download_url": f"{base_url}/api/stream/{torrent_id}/download/{default_index}",
        "links": links,
    }


def _status_for_torrent(torrent_id, request: Request):
    torrent_id, torrent_info = _torrent_or_404(torrent_id)
    return _public_torrent(get_torrent_status(torrent_info), request)


def _magnet_from_request(payload: PublicTorrentAddRequest):
    magnet = payload.magnet or payload.magnet_link or payload.link
    if not magnet or not magnet.startswith("magnet:?"):
        raise HTTPException(status_code=400, detail="Request body must include magnet, magnet_link, or link")
    return magnet


def _compat_torrent(public_torrent):
    torrent_id = public_torrent["id"]
    compat_files = []
    for file_info in public_torrent.get("files", []):
        compat_file = dict(file_info)
        compat_file["length"] = compat_file.get("size", 0)
        compat_file["link"] = (
            f"/api/v1/torrents/{torrent_id}/stream.m3u8"
            if compat_file.get("is_default_stream")
            else f"/api/v1/torrents/{torrent_id}/playlist.m3u"
        )
        compat_files.append(compat_file)

    compat_torrent = dict(public_torrent)
    compat_torrent["files"] = compat_files
    return compat_torrent


async def _add_torrent_from_payload(payload: PublicTorrentAddRequest, request: Request):
    result = await torrents.add_torrent(torrents.TorrentAddRequest(magnet_link=_magnet_from_request(payload)))
    torrent_id = result.get("torrent_id")
    if not torrent_id:
        raise HTTPException(status_code=500, detail="Torrent was added but no torrent id was returned")

    public_torrent = _status_for_torrent(torrent_id, request)
    public_torrent["message"] = result.get("message", "Torrent added")
    return public_torrent


def _playlist_response(content):
    return Response(
        content=content,
        media_type="audio/x-mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@router.get("")
async def get_api_index(request: Request):
    base_url = _base_url(request)
    return {
        "name": "Torrent Streamer Public API",
        "version": "1",
        "endpoints": {
            "add_torrent": f"{base_url}/api/v1/torrents",
            "list_torrents": f"{base_url}/api/v1/torrents",
            "torrent_details": f"{base_url}/api/v1/torrents/{{torrent_id}}",
            "hls_stream": f"{base_url}/api/v1/torrents/{{torrent_id}}/stream.m3u8",
            "external_player_playlist": f"{base_url}/api/v1/torrents/{{torrent_id}}/playlist.m3u",
            "file_stream": f"{base_url}/api/stream/{{torrent_id}}?file_index={{file_index}}",
            "file_download": f"{base_url}/api/stream/{{torrent_id}}/download/{{file_index}}",
        },
    }


@router.post("/torrents", status_code=202)
async def add_public_torrent(payload: PublicTorrentAddRequest, request: Request):
    return await _add_torrent_from_payload(payload, request)


@router.get("/torrents")
async def list_public_torrents(request: Request):
    return [
        _public_torrent(get_torrent_status(torrent_info), request)
        for torrent_info in active_torrents.values()
    ]


@router.get("/torrents/{torrent_id}")
async def get_public_torrent(torrent_id: str, request: Request):
    return _status_for_torrent(torrent_id, request)


@router.delete("/torrents/{torrent_id}")
async def delete_public_torrent(torrent_id: str):
    return await torrents.remove_torrent(torrent_id)


@router.get("/torrents/{torrent_id}/stream.m3u8")
async def get_public_stream(torrent_id: str, request: Request, segment: int = Query(0, ge=0), file_index: Optional[int] = Query(None, ge=0)):
    torrent = _status_for_torrent(torrent_id, request)
    default_index = torrent.get("default_file", {}).get("index", 0)
    resolved_index = default_index if file_index is None else file_index
    stream_url = f"{_base_url(request)}/api/stream/{torrent_id}?segment={segment}&file_index={resolved_index}"
    return RedirectResponse(stream_url, status_code=307)


@router.get("/torrents/{torrent_id}/playlist.m3u")
async def get_public_playlist(torrent_id: str, request: Request, file_index: Optional[int] = Query(None, ge=0)):
    torrent = _status_for_torrent(torrent_id, request)
    title = torrent.get("name") or torrent_id
    default_index = torrent.get("default_file", {}).get("index", 0)
    resolved_index = default_index if file_index is None else file_index
    return _playlist_response(
        "#EXTM3U\n"
        f"#EXTINF:-1,{title}\n"
        f"{torrent['files'][resolved_index]['stream_url'] if torrent.get('files') and resolved_index < len(torrent['files']) else torrent['stream_url']}\n"
    )


@router.get("/torrents/{torrent_id}/files/{file_index}/stream.m3u8")
async def get_public_file_stream(torrent_id: str, file_index: int, request: Request, segment: int = Query(0, ge=0)):
    torrent_id, _ = _torrent_or_404(torrent_id)
    stream_url = f"{_base_url(request)}/api/stream/{torrent_id}?segment={segment}&file_index={file_index}"
    return RedirectResponse(stream_url, status_code=307)


@router.get("/torrents/{torrent_id}/files/{file_index}/download")
async def get_public_file_download(torrent_id: str, file_index: int, request: Request):
    torrent_id, _ = _torrent_or_404(torrent_id)
    download_url = f"{_base_url(request)}/api/stream/{torrent_id}/download/{file_index}"
    return RedirectResponse(download_url, status_code=307)


@compat_router.post("/torrents", status_code=202)
async def add_compat_torrent(payload: PublicTorrentAddRequest, request: Request):
    torrent = await _add_torrent_from_payload(payload, request)
    return [_compat_torrent(torrent)]


@compat_router.get("/torrents")
async def list_compat_torrents(request: Request):
    return [_compat_torrent(torrent) for torrent in await list_public_torrents(request)]


@compat_router.get("/torrents/{torrent_id}")
async def get_compat_torrent(torrent_id: str, request: Request):
    return _compat_torrent(_status_for_torrent(torrent_id, request))


@compat_router.get("/torrents/{torrent_id}/files")
async def get_compat_playlist(torrent_id: str, request: Request):
    return await get_public_playlist(torrent_id, request)