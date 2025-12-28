import time
from pathlib import Path
from src.config.config import load_config, ChannelConfig
from src.logger import setup_logger
from src.state import StateRepository
from src.infra.yt_dlp import fetch_channel_videos, download_video
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService

def test_latest_sequential_repost():
    # 1. 初始化配置与服务
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)
    
    # 定义目标频道
    test_channel = ChannelConfig(
        name="Test_Channel",
        yt_channel_id="UCuudpdbKmQWq2PPzYgVCWlA",
        bili_tags=["测试"],
        bili_tid=174, 
        title_prefix="【最新测试】",
        enabled=True
    )

    translator = TranslatorService()
    uploader = UploaderService(config)

    try:
        # 2. 获取视频列表 (使用较小的 limit 以免卡顿，fetch_channel_videos 应已加入 --flat-playlist)
        logger.info(f"正在获取频道 {test_channel.yt_channel_id} 的最新视频列表...")
        videos = fetch_channel_videos(test_channel.yt_channel_id, limit=20) 
        
        # 3. 寻找“下一个”待转载视频
        # videos 列表索引 0 是最新发布的，索引增大时间越久
        target_video = None
        for v in videos:
            if not state.exists(v["id"]):
                target_video = v
                break # 找到最新且未处理的一个，直接跳出循环

        if not target_video:
            logger.info("当前列表中的视频已全部处理完毕。")
            return

        vid = target_video["id"]
        logger.info(f"发现待处理的最新视频: {target_video['title']} (ID: {vid})")

        # 4. 执行流水线
        # 下载
        out_path = Path(config.download_dir) / f"{vid}.mp4"
        download_video(target_video["webpage_url"], str(out_path))
        state.mark_downloaded(vid)
        
        # 翻译
        new_title = translator.translate(target_video["title"], test_channel.title_prefix)
        
        # 上传
        bvid = uploader.upload(out_path, new_title, target_video, test_channel)
        
        # 5. 更新状态
        state.mark_uploaded(vid, bvid)
        logger.info(f"转载成功！B站 BV号: {bvid}")

    except Exception as e:
        logger.error(f"测试运行发生错误: {e}")

if __name__ == "__main__":
    test_latest_sequential_repost()