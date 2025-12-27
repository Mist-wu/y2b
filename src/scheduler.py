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

    def init_ignore_existing(self):
        self.logger.info("正在初始化：扫描并忽略各频道当前已存在的视频...")
        for channel in self.config.channels:
            if not channel.enabled:
                continue
            
            try:
                videos = self.monitor.get_new_videos(channel, self.state)
                for v in videos:
                    self.state.mark_skipped(v["id"])
                    self.logger.info(f"已忽略历史视频: {v['title']} (ID: {v['id']})")
            except Exception as e:
                self.logger.error(f"初始化频道 {channel.name} 失败: {e}")
        
        self.logger.info("初始化完成，开始监听新视频。")

    def process_video(self, video, channel):
        vid = video["id"]
        for attempt in range(1, self.config.max_retry + 1):
            try:
                self.logger.info(f"processing {vid} (Attempt {attempt}/{self.config.max_retry})")
                
                path = self.downloader.download(video, self.config.download_dir)
                self.state.mark_downloaded(vid)

                title = self.translator.translate(video["title"], channel.title_prefix)
                bvid = self.uploader.upload(path, title, video, channel)

                self.state.mark_uploaded(vid, bvid)
                return  

            except Exception as e:
                self.logger.error(f"{vid} failed attempt {attempt}: {e}")
                
                if attempt == self.config.max_retry:
                    self.state.mark_failed(vid, str(e))
                else:
                    time.sleep(5)