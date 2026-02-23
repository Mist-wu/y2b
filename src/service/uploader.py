from src.infra.biliup import upload

class UploaderService:
    def __init__(self, config):
        self.config = config

    def upload(self, video_path, title, video, channel):
        desc = f"""原视频标题：{video['title']}
原视频链接：{video['webpage_url']}
频道：{channel.name}
"""
        return upload(
            executable=self.config.bilibili.executable,
            user_cookie_arg=self.config.bilibili.user_cookie_arg,
            video_path=str(video_path),
            title=title,
            desc=desc,
            tags=channel.bili_tags,
            tid=channel.bili_tid,
            user_cookie=self.config.bilibili_cookies,
            upload_cfg=self.config.bilibili_upload,
            extra_args=self.config.bilibili.extra_args,
        )
