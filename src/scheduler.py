import os
import time
from pathlib import Path

from src.infra.yt_dlp import YOUTUBE_COOKIES_PATH
from src.service.downloader import DownloaderService
from src.service.monitor import MonitorService
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService


class Scheduler:
    def __init__(self, config, logger, state, startup_cutoff_ts: int | None = None):
        self.config = config
        self.logger = logger
        self.state = state
        self.startup_ts = self.state.set_run_startup_ts(startup_cutoff_ts)

        self.monitor = MonitorService(youtube_cookies_path=YOUTUBE_COOKIES_PATH)
        self.downloader = DownloaderService(youtube_cookies_path=YOUTUBE_COOKIES_PATH)
        self.translator = TranslatorService(config, logger)
        self.uploader = UploaderService(config)

    def run(self):
        self.logger.info(
            f"启动完成，仅处理发布时间晚于启动时间的视频。startup_cutoff_ts={self.startup_ts}"
        )
        while True:
            for channel in self.config.channels:
                if not channel.enabled:
                    continue

                try:
                    videos = self.monitor.get_new_videos(
                        channel,
                        self.state,
                        startup_ts=self.startup_ts,
                        scan_limit=self.config.monitor_scan_limit,
                        logger=self.logger,
                    )
                except Exception as e:
                    self.logger.error(f"拉取频道视频失败 {channel.name}: {e}")
                    continue

                for v in videos:
                    self.process_video(v, channel)

            self.logger.info(f"轮询结束，等待 {self.config.poll_interval} 秒...")
            time.sleep(self.config.poll_interval)

    def process_video(self, video, channel):
        vid = video["id"]
        out_path = None
        self.state.mark_queued(video)

        for attempt in range(1, self.config.max_retry + 1):
            try:
                self.logger.info(
                    f"开始处理 {vid} ({video.get('title')}) (尝试 {attempt}/{self.config.max_retry})"
                )

                self.state.mark_downloading(video)
                out_path = self.downloader.download(video, self.config.download_dir)
                self.state.mark_downloaded(video)

                title = self.translator.translate(video["title"], channel.title_prefix)
                bvid = self.uploader.upload(out_path, title, video, channel)

                self.state.mark_uploaded(video, bvid)
                self.logger.info(f"视频 {vid} 搬运成功: {bvid}")
                return

            except Exception as e:
                retryable = attempt < self.config.max_retry
                self.logger.error(f"视频 {vid} 处理失败 (第 {attempt} 次): {e}")
                self.state.mark_failed(video, str(e), retryable=retryable)
                if retryable:
                    time.sleep(5)

            finally:
                if out_path and Path(out_path).exists():
                    try:
                        os.remove(out_path)
                        self.logger.info(f"已清理临时文件: {out_path}")
                    except Exception as cleanup_err:
                        self.logger.error(f"清理失败: {cleanup_err}")
                out_path = None
