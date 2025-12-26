import time
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
        self.uploader = UploaderService()

    def run(self):
        while True:
            for channel in self.config.channels:
                if not channel.enabled:
                    continue

                videos = self.monitor.get_new_videos(channel, self.state)
                for v in videos:
                    vid = v["id"]
                    try:
                        self.logger.info(f"processing {vid}")
                        path = self.downloader.download(v, self.config.download_dir)
                        self.state.mark_downloaded(vid)

                        title = self.translator.translate(v["title"], channel.title_prefix)
                        bvid = self.uploader.upload(path, title, v, channel)

                        self.state.mark_uploaded(vid, bvid)
                    except Exception as e:
                        self.logger.error(f"{vid} failed: {e}")
                        self.state.mark_failed(vid, str(e))

            time.sleep(self.config.poll_interval)
