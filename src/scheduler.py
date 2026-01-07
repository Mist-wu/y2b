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
        # 启动主循环前，先执行一次初始化扫描
        self.init_ignore_existing()

        while True:
            for channel in self.config.channels:
                if not channel.enabled:
                    continue

                try:
                    # 获取新视频
                    videos = self.monitor.get_new_videos(channel, self.state)
                except Exception as e:
                    self.logger.error(f"Failed to fetch videos for {channel.name}: {e}")
                    continue

                for v in videos:
                    self.process_video(v, channel)

            self.logger.info(f"轮询结束，等待 {self.config.poll_interval} 秒...")
            time.sleep(self.config.poll_interval)

    def init_ignore_existing(self):
        """
        初始化：将频道现有的视频全部标记为已跳过，防止程序第一次启动时搬运大量历史视频。
        """
        self.logger.info("正在初始化：扫描并忽略各频道当前已存在的视频...")
        for channel in self.config.channels:
            if not channel.enabled:
                continue
            
            try:
                # 默认获取最新 3 条进行标记
                videos = self.monitor.get_new_videos(channel, self.state)
                for v in videos:
                    self.state.mark_skipped(v["id"])
                    self.logger.info(f"已忽略历史视频: {v['title']} (ID: {v['id']})")
            except Exception as e:
                self.logger.error(f"初始化频道 {channel.name} 失败: {e}")
        
        self.logger.info("初始化完成，开始监听新视频。")

    def process_video(self, video, channel):
        vid = video["id"]
        out_path = None
        
        for attempt in range(1, self.config.max_retry + 1):
            try:
                self.logger.info(f"开始处理 {vid} (尝试 {attempt}/{self.config.max_retry})")
                
                # 1. 下载
                out_path = self.downloader.download(video, self.config.download_dir)
                self.state.mark_downloaded(vid)

                # 2. 翻译标题
                title = self.translator.translate(video["title"], channel.title_prefix)
                
                # 3. 上传
                bvid = self.uploader.upload(out_path, title, video, channel)

                # 4. 成功记录
                self.state.mark_uploaded(vid, bvid)
                self.logger.info(f"视频 {vid} 搬运成功: {bvid}")
                return  

            except Exception as e:
                self.logger.error(f"视频 {vid} 处理失败 (第 {attempt} 次): {e}")
                if attempt == self.config.max_retry:
                    self.state.mark_failed(vid, str(e))
                else:
                    time.sleep(5)
            
            finally:
                # 自动清理 MP4 文件
                if out_path and Path(out_path).exists():
                    try:
                        os.remove(out_path)
                        self.logger.info(f"已清理临时文件: {out_path}")
                    except Exception as cleanup_err:
                        self.logger.error(f"清理失败: {cleanup_err}")