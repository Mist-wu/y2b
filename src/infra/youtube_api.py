from __future__ import annotations

import datetime as dt
import json
import os
import urllib.parse
import urllib.request


BASE_URL = "https://www.googleapis.com/youtube/v3"


def _require_api_key(api_key_env: str) -> str:
    key = (os.getenv(api_key_env) or "").strip()
    if not key:
        raise RuntimeError(f"未检测到 YouTube Data API key 环境变量: {api_key_env}")
    return key


def _request_json(path: str, params: dict[str, str], *, api_key: str, timeout: int = 20) -> dict:
    query = dict(params)
    query["key"] = api_key
    url = f"{BASE_URL}/{path}?{urllib.parse.urlencode(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "y2b-monitor/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except Exception as e:
        raise RuntimeError(f"YouTube Data API 请求失败 ({path}): {e}") from e

    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"YouTube Data API 返回非 JSON ({path}): {e}") from e


def _iso_to_ts(value: str | None) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


def probe_youtube_data_api_access(channel_id: str, *, api_key_env: str) -> None:
    api_key = _require_api_key(api_key_env)
    uploads_id = get_uploads_playlist_id(channel_id, api_key=api_key)
    items = fetch_upload_playlist_items(uploads_id, api_key=api_key, max_results=1)
    if items is None:
        raise RuntimeError("YouTube Data API 探针失败：未返回数据")


def get_uploads_playlist_id(channel_id: str, *, api_key: str | None = None, api_key_env: str = "YOUTUBE_DATA_API_KEY") -> str:
    key = api_key or _require_api_key(api_key_env)
    data = _request_json(
        "channels",
        {
            "part": "contentDetails",
            "id": channel_id,
            "fields": "items(id,contentDetails/relatedPlaylists/uploads)",
            "maxResults": "1",
        },
        api_key=key,
    )
    items = data.get("items") or []
    if not items:
        raise RuntimeError(f"YouTube Data API 未找到频道: {channel_id}")
    uploads = (
        items[0].get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads")
    )
    uploads = str(uploads or "").strip()
    if not uploads:
        raise RuntimeError(f"YouTube Data API 未返回 uploads 播放列表ID: {channel_id}")
    return uploads


def fetch_upload_playlist_items(
    uploads_playlist_id: str,
    *,
    api_key: str | None = None,
    api_key_env: str = "YOUTUBE_DATA_API_KEY",
    max_results: int = 10,
) -> list[dict]:
    key = api_key or _require_api_key(api_key_env)
    data = _request_json(
        "playlistItems",
        {
            "part": "snippet,contentDetails,status",
            "playlistId": uploads_playlist_id,
            "maxResults": str(max(1, min(int(max_results), 50))),
            "fields": (
                "items("
                "snippet(title,publishedAt,resourceId/videoId,videoOwnerChannelId),"
                "contentDetails(videoId,videoPublishedAt),"
                "status/privacyStatus"
                ")"
            ),
        },
        api_key=key,
    )
    return list(data.get("items") or [])


def normalize_playlist_item(item: dict, *, fallback_channel_id: str) -> dict | None:
    snippet = item.get("snippet") or {}
    content = item.get("contentDetails") or {}
    status = item.get("status") or {}
    video_id = str(content.get("videoId") or (snippet.get("resourceId") or {}).get("videoId") or "").strip()
    if not video_id:
        return None

    title = str(snippet.get("title") or "").strip() or f"video_{video_id}"
    privacy = str(status.get("privacyStatus") or "").strip().lower() or None
    published_at = str(content.get("videoPublishedAt") or snippet.get("publishedAt") or "").strip()
    published_ts = _iso_to_ts(published_at)
    channel_id = str(snippet.get("videoOwnerChannelId") or fallback_channel_id or "").strip() or fallback_channel_id

    return {
        "id": video_id,
        "title": title,
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "channel_id": channel_id,
        "published_ts": published_ts,
        "availability": privacy or "public",
        "live_status": None,
        "is_live": False,
        "_api_privacy_status": privacy,
    }
