from src.infra.yt_dlp import fetch_channel_videos

class MonitorService:
    def get_new_videos(self, channel, state):
        videos = fetch_channel_videos(channel.yt_channel_id)
        result = []
        for v in videos:
            if not state.exists(v["id"]):
                result.append(v)
        return result
