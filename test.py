import time
from pathlib import Path
from src.config.config import load_config, ChannelConfig
from src.logger import setup_logger
from src.state import StateRepository
from src.infra.yt_dlp import fetch_channel_videos, download_video
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService

def test_single_repost():
    # 1. 初始化
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)
    
    # 2. 定义测试频道 (ID: UCuudpdbKmQWq2PPzYgVCWlA)
    test_channel = ChannelConfig(
        name="Test_Channel",
        yt_channel_id="UCuudpdbKmQWq2PPzYgVCWlA",
        bili_tags=["测试"],
        bili_tid=174, # 默认生活分区，可根据需要修改
        title_prefix="【测试转载】",
        enabled=True
    )

    translator = TranslatorService()
    uploader = UploaderService(config)

    try:
        # 3. 获取该频道所有视频 (解除 limit 限制以获取完整列表)
        logger.info(f"正在获取频道 {test_channel.yt_channel_id} 的视频列表...")
        # 注意：此处 fetch_channel_videos 需设置较大 limit 或修改源码以获取全部
        videos = fetch_channel_videos(test_channel.yt_channel_id, limit=100) 
        
        # 4. 过滤已存在的视频并按时间排序 (yt-dlp 返回通常是倒序，反转即为正序)
        pending_videos = [v for v in videos if not state.exists(v["id"])]
        pending_videos.reverse() # 现在列表第一个是最早的视频

        if not pending_videos:
            logger.info("没有发现新视频或所有视频已转载完成。")
            return

        # 5. 提取“下一个”最早的视频
        target_video = pending_videos[0]
        vid = target_video["id"]
        logger.info(f"准备转载最早的未处理视频: {target_video['title']} (ID: {vid})")

        # 6. 执行下载 -> 翻译 -> 上传
        # 下载
        out_path = Path(config.download_dir) / f"{vid}.mp4"
        download_video(target_video["webpage_url"], str(out_path))
        state.mark_downloaded(vid)
        
        # 翻译标题
        new_title = translator.translate(target_video["title"], test_channel.title_prefix)
        
        # 上传
        bvid = uploader.upload(out_path, new_title, target_video, test_channel)
        
        # 7. 记录状态
        state.mark_uploaded(vid, bvid)
        logger.info(f"测试转载成功！Bilibili ID: {bvid}")

    except Exception as e:
        logger.error(f"测试运行失败: {e}")

if __name__ == "__main__":
    test_single_repost()