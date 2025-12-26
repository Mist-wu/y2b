from src.infra.biliup import upload

class UploaderService:
    def upload(self, video_path, title, video, channel):
        desc = f"""原视频标题：{video['title']}
原视频链接：{video['webpage_url']}
频道：{channel.name}
"""
        return upload(
            video_path=str(video_path),
            title=title,
            desc=desc,
            tags=channel.bili_tags,
            tid=channel.bili_tid
        )
