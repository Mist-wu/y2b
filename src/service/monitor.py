from __future__ import annotations

import datetime as dt
import time
from typing import Any

from src.infra.youtube_api import (
    fetch_upload_playlist_items,
    get_uploads_playlist_id,
    normalize_playlist_item,
)
from src.infra.yt_dlp import fetch_channel_video_heads, fetch_video_metadata


NON_PUBLIC_AVAILABILITY = {
    "private",
    "needs_auth",
    "subscriber_only",
    "premium_only",
    "unlisted",
}


class MonitorService:
    def __init__(
        self,
        *,
        monitor_backend: str = "yt_dlp",
        youtube_api_key_env: str = "YOUTUBE_DATA_API_KEY",
        youtube_cookies_path: str | None,
        youtube_cookies_from_browser: str | None,
        youtube_extractor_args: list[str] | None = None,
    ):
        self.monitor_backend = (monitor_backend or "yt_dlp").lower()
        self.youtube_api_key_env = youtube_api_key_env
        self.youtube_cookies_path = youtube_cookies_path
        self.youtube_cookies_from_browser = youtube_cookies_from_browser
        self.youtube_extractor_args = youtube_extractor_args or []
        self._uploads_playlist_cache: dict[str, str] = {}

    def get_new_videos(self, channel, state, *, startup_ts: int, scan_limit: int, logger=None):
        if self.monitor_backend == "youtube_api":
            return self._get_new_videos_from_api(channel, state, startup_ts=startup_ts, scan_limit=scan_limit)
        return self._get_new_videos_from_ytdlp(channel, state, startup_ts=startup_ts, scan_limit=scan_limit)

    def _get_new_videos_from_api(self, channel, state, *, startup_ts: int, scan_limit: int):
        raw_entries = self._fetch_api_entries(channel.yt_channel_id, scan_limit=scan_limit)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for raw in raw_entries:
            video = self._normalize_api_item(raw, channel.yt_channel_id)
            if video is None:
                continue
            video_id = video["id"]
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            status = state.get_status(video_id)
            if status and not state.can_process(video_id):
                continue

            published_ts = video.get("published_ts")
            if published_ts is None:
                if state.can_process(video_id):
                    state.mark_skipped_filtered(video, "missing_published_time")
                continue

            if published_ts <= startup_ts:
                if state.can_process(video_id):
                    state.mark_skipped_before_start(video)
                continue

            if state.can_process(video_id):
                results.append(video)

        results.sort(key=lambda v: (v.get("published_ts") or 0, v["id"]))
        return results

    def _get_new_videos_from_ytdlp(self, channel, state, *, startup_ts: int, scan_limit: int):
        raw_heads = self._fetch_with_backfill(channel.yt_channel_id, scan_limit=scan_limit)
        results: list[dict[str, Any]] = []
        now_ts = int(time.time())
        seen_ids: set[str] = set()

        for head in raw_heads:
            video_id = str(head.get("id") or "").strip()
            if not video_id:
                continue
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            status = state.get_status(video_id)
            if status and not state.can_process(video_id):
                continue

            raw = self._fetch_video_detail(head)
            video = self._normalize_video(raw, channel.yt_channel_id)
            if video is None:
                continue

            skip_reason = self._filter_reason(video, raw, now_ts)
            if skip_reason:
                if state.can_process(video["id"]):
                    state.mark_skipped_filtered(video, skip_reason)
                continue

            published_ts = video.get("published_ts")
            if published_ts is None:
                if state.can_process(video["id"]):
                    state.mark_skipped_filtered(video, "missing_published_time")
                continue

            if published_ts <= startup_ts:
                if state.can_process(video["id"]):
                    state.mark_skipped_before_start(video)
                continue

            if state.can_process(video["id"]):
                results.append(video)

        results.sort(key=lambda v: (v.get("published_ts") or 0, v["id"]))
        return results

    def _fetch_api_entries(self, channel_id: str, *, scan_limit: int) -> list[dict]:
        uploads_playlist_id = self._uploads_playlist_cache.get(channel_id)
        if not uploads_playlist_id:
            uploads_playlist_id = get_uploads_playlist_id(channel_id, api_key_env=self.youtube_api_key_env)
            self._uploads_playlist_cache[channel_id] = uploads_playlist_id
        return fetch_upload_playlist_items(
            uploads_playlist_id,
            api_key_env=self.youtube_api_key_env,
            max_results=max(1, min(scan_limit, 50)),
        )

    def _fetch_with_backfill(self, channel_id: str, *, scan_limit: int) -> list[dict]:
        # Page through recent uploads to reduce miss risk when a channel posts several videos between polls.
        page_size = min(max(scan_limit, 1), 25)
        max_total = max(scan_limit, 1)
        fetched: list[dict] = []
        playlist_start = 1

        while len(fetched) < max_total:
            need = min(page_size, max_total - len(fetched))
            page = fetch_channel_video_heads(
                channel_id,
                limit=need,
                playlist_start=playlist_start,
                cookies_path=self.youtube_cookies_path,
                cookies_from_browser=self.youtube_cookies_from_browser,
                extractor_args=self.youtube_extractor_args,
            )
            if not page:
                break
            fetched.extend(page)
            if len(page) < need:
                break
            playlist_start += need

        return fetched

    def _fetch_video_detail(self, head: dict[str, Any]) -> dict[str, Any]:
        video_id = str(head.get("id") or "").strip()
        if not video_id:
            return head
        url = head.get("url") or head.get("webpage_url") or video_id
        raw = fetch_video_metadata(
            str(url),
            cookies_path=self.youtube_cookies_path,
            cookies_from_browser=self.youtube_cookies_from_browser,
            extractor_args=self.youtube_extractor_args,
        )
        # 补齐平铺列表里可能带的字段（有些情况下详情缺失）
        for k, v in head.items():
            raw.setdefault(k, v)
        return raw

    def _normalize_video(self, raw: dict[str, Any], channel_id: str) -> dict[str, Any] | None:
        video_id = raw.get("id")
        if not video_id:
            return None

        webpage_url = (
            raw.get("webpage_url")
            or raw.get("original_url")
            or raw.get("url")
        )

        return {
            "id": video_id,
            "title": (raw.get("title") or "").strip() or f"video_{video_id}",
            "webpage_url": webpage_url,
            "channel_id": raw.get("channel_id") or channel_id,
            "published_ts": self._extract_published_ts(raw),
            "availability": raw.get("availability"),
            "live_status": raw.get("live_status"),
            "is_live": raw.get("is_live"),
        }

    def _normalize_api_item(self, raw: dict[str, Any], channel_id: str) -> dict[str, Any] | None:
        return normalize_playlist_item(raw, fallback_channel_id=channel_id)

    def _extract_published_ts(self, raw: dict[str, Any]) -> int | None:
        for key in ("release_timestamp", "timestamp"):
            val = raw.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return int(val)

        upload_date = raw.get("upload_date")
        if isinstance(upload_date, str) and len(upload_date) == 8 and upload_date.isdigit():
            try:
                dt_obj = dt.datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=dt.timezone.utc)
                return int(dt_obj.timestamp())
            except ValueError:
                return None
        return None

    def _filter_reason(self, video: dict[str, Any], raw: dict[str, Any], now_ts: int) -> str | None:
        url = str(video.get("webpage_url") or "")
        if "/shorts/" in url.lower():
            return "shorts"

        availability = raw.get("availability")
        if availability in NON_PUBLIC_AVAILABILITY:
            return f"availability:{availability}"

        live_status = str(raw.get("live_status") or "").lower()
        if live_status in {"is_upcoming", "upcoming"}:
            return "upcoming_live"
        if live_status in {"is_live", "live"} or raw.get("is_live") is True:
            return "live_now"

        published_ts = video.get("published_ts")
        if isinstance(published_ts, int) and published_ts > now_ts + 300:
            return "not_published_yet"

        return None
