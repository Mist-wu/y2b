import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from src.config.config import load_config, ChannelConfig
from src.logger import setup_logger
from src.state import StateRepository
from src.infra.yt_dlp import fetch_channel_videos, download_video
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService

def run_quick_test():
    # 1. 加载配置与初始化环境
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)
    
    test_channel = ChannelConfig(
        name="Test_Quick",
        yt_channel_id="UCuudpdbKmQWq2PPzYgVCWlA",
        bili_tags=["测试"],
        bili_tid=174, # 生活分区
        title_prefix="【测试】",
        enabled=True
    )

    translator = TranslatorService()
    uploader = UploaderService(config)

    try:
        # 3. 抓取最新视频列表 (limit设小以防卡顿)
        logger.info(f"正在获取频道 {test_channel.yt_channel_id} 的最新视频...")
        # 建议确保 src/infra/yt_dlp.py 中已添加 --flat-playlist 参数
        videos = fetch_channel_videos(test_channel.yt_channel_id, limit=5) 
        
        # 4. 寻找第一个未处理的视频
        target_video = None
        for v in videos:
            if not state.exists(v["id"]):
                target_video = v
                break

        if not target_video:
            logger.info("未发现新视频，请手动删除 state.db 或更换测试频道。")
            return

        vid = target_video["id"]
        logger.info(f"开始处理视频: {target_video['title']} (ID: {vid})")


        logger.info("步骤 1: 正在下载...")
        out_path = Path(config.download_dir) / f"{vid}.mp4"
        download_video(target_video["webpage_url"], str(out_path))
        state.mark_downloaded(vid)
        
        # B. 翻译
        logger.info("步骤 2: 正在调用 AI 翻译标题...")
        new_title = translator.translate(target_video["title"], test_channel.title_prefix)
        logger.info(f"翻译结果: {new_title}")
        
        # C. 上传
        logger.info("步骤 3: 正在上传至 Bilibili...")
        bvid = uploader.upload(out_path, new_title, target_video, test_channel)
        
        # 6. 成功记录
        state.mark_uploaded(vid, bvid)
        logger.info(f"测试圆满成功！B站视频号: {bvid}")

    except Exception as e:
        logger.error(f"测试失败，错误原因: {e}")
        # 打印详细错误堆栈方便排查
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_quick_test()