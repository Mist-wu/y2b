import sys
import argparse
import os
from pathlib import Path

# 将项目根目录添加到路径
sys.path.append(str(Path(__file__).parent))

from src.config.config import load_config, ChannelConfig
from src.logger import setup_logger
from src.state import StateRepository
from src.infra.yt_dlp import fetch_channel_videos, download_video
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService

def run_specific_repost(target_channel_id: str):
    # 1. 初始化
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)
    translator = TranslatorService()
    uploader = UploaderService(config)

    # 2. 匹配频道配置
    channel_cfg = next((c for c in config.channels if c.yt_channel_id == target_channel_id), None)
    
    if not channel_cfg:
        logger.warning(f"未在配置中找到 ID {target_channel_id}，使用默认参数。")
        channel_cfg = ChannelConfig(
            name="Manual_Task",
            yt_channel_id=target_channel_id,
            bili_tags=["测试"],
            bili_tid=174,
            title_prefix="【搬运】",
            enabled=True
        )

    out_path = None # 初始化文件路径变量

    try:
        # 3. 获取最新视频
        logger.info(f"正在获取频道 {target_channel_id} 的最新视频...")
        videos = fetch_channel_videos(channel_cfg.yt_channel_id, limit=1)
        
        if not videos:
            logger.error("未找到任何视频。")
            return

        video = videos[0]
        vid = video["id"]

        if state.exists(vid):
            logger.info(f"视频 {vid} 已存在，跳过。")
            return

        # 4. 执行搬运
        logger.info(f"开始搬运: {video['title']}")

        # A. 下载
        out_path = Path(config.download_dir) / f"{vid}.mp4"
        download_video(video["webpage_url"], str(out_path))
        state.mark_downloaded(vid)

        # B. 翻译
        new_title = translator.translate(video["title"], channel_cfg.title_prefix)

        # C. 上传
        bvid = uploader.upload(out_path, new_title, video, channel_cfg)
        
        state.mark_uploaded(vid, bvid)
        logger.info(f"成功！B站 ID: {bvid}")

    except Exception as e:
        logger.error(f"异常: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 5. 自动清理已下载文件
        if out_path and out_path.exists():
            try:
                os.remove(out_path)
                logger.info(f"已自动清理临时文件: {out_path}")
            except Exception as cleanup_err:
                logger.error(f"清理文件失败: {cleanup_err}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="搬运指定 YouTube 频道的最新视频")
    parser.add_argument("id", type=str, help="YouTube 频道 ID")
    args = parser.parse_args()
    run_specific_repost(args.id)