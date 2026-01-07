import time
import os
from pathlib import Path
from src.service.monitor import MonitorService
from src.service.downloader import DownloaderService
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService

class Scheduler:
    def __init__(self, config, logger, state):
        self.config = config
        self.logger = logger
        self.state = state

        self.monitor = MonitorService()
        self.downloader = DownloaderService()
        self.translator = TranslatorService()
        self.uploader = UploaderService(config)  

    def run(self):
        # [新增] 启动主循环前，先执行一次初始化扫描
        self.init_ignore_existing()

        while True:
            for channel in self.config.channels:
                if not channel.enabled:
                    continue

                try:
                    videos = self.monitor.get_new_videos(channel, self.state)
                except Exception as e:
                    self.logger.error(f"Failed to fetch videos for {channel.name}: {e}")
                    continue

                for v in videos:
                    self.process_video(v, channel)

            time.sleep(self.config.poll_interval)

    def process_video(self, video, channel):
        vid = video["id"]
        path = None
        for attempt in range(1, self.config.max_retry + 1):
            try:
                self.logger.info(f"processing {vid} (Attempt {attempt})")
                
                path = self.downloader.download(video, self.config.download_dir)
                self.state.mark_downloaded(vid)

                title = self.translator.translate(video["title"], channel.title_prefix)
                bvid = self.uploader.upload(path, title, video, channel)

                self.state.mark_uploaded(vid, bvid)
                break # 成功则退出重试循环

            except Exception as e:
                self.logger.error(f"{vid} attempt {attempt} failed: {e}")
                if attempt == self.config.max_retry:
                    self.state.mark_failed(vid, str(e))
                time.sleep(5)
            
            finally:
                # 无论成功还是最后一次重试失败，只要文件存在就清理
                if path and Path(path).exists():
                    try:
                        os.remove(path)
                        self.logger.info(f"Cleaned up: {path}")
                    except:
                        pass