import argparse
import sys
import time
from pathlib import Path

# 将项目根目录添加到路径
sys.path.append(str(Path(__file__).parent))

from src.config.config import load_config, ChannelConfig
from src.bootstrap import prepare_runtime
from src.infra.yt_dlp import probe_youtube_access
from src.logger import setup_logger
from src.state import StateRepository
from src.scheduler import Scheduler


def _resolve_channel_config(config, logger, target_channel_id: str) -> ChannelConfig:
    channel_cfg = next((c for c in config.channels if c.yt_channel_id == target_channel_id), None)
    if channel_cfg:
        return channel_cfg

    logger.warning(f"未在配置中找到频道 ID {target_channel_id}，使用默认测试参数。")
    return ChannelConfig(
        name="Manual_Test",
        yt_channel_id=target_channel_id,
        bili_tags=["测试"],
        bili_tid=174,
        title_prefix="【搬运测试】",
        enabled=True,
    )


def _pick_latest_unuploaded_video(scheduler: Scheduler, channel_cfg: ChannelConfig, state, logger, *, scan_limit: int):
    monitor = scheduler.monitor
    raw_heads = monitor._fetch_with_backfill(channel_cfg.yt_channel_id, scan_limit=scan_limit)
    if not raw_heads:
        return None

    logger.info(f"测试模式扫描频道最近 {len(raw_heads)} 条候选视频（scan_limit={scan_limit}）")
    now_ts = int(time.time())
    seen_ids: set[str] = set()
    bot_block_count = 0

    for idx, head in enumerate(raw_heads, start=1):
        video_id = str(head.get("id") or "").strip()
        if not video_id or video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        status = state.get_status(video_id)
        if status == "uploaded":
            logger.info(f"[{idx}] 跳过已上传视频 {video_id}")
            continue

        try:
            raw = monitor._fetch_video_detail(head)
            video = monitor._normalize_video(raw, channel_cfg.yt_channel_id)
            bot_block_count = 0
        except Exception as e:
            err_text = str(e)
            if "confirm you’re not a bot" in err_text or "confirm you're not a bot" in err_text:
                bot_block_count += 1
                logger.warning(f"[{idx}] 拉取详情失败 {video_id}: YouTube 风控拦截（连续 {bot_block_count} 次）")
                if bot_block_count >= 3:
                    raise RuntimeError(
                        "测试中止：连续 3 次视频详情请求触发 YouTube 风控。"
                        "建议改用 global.youtube.cookies_from_browser（edge/chrome）后重试。"
                    ) from e
            else:
                logger.warning(f"[{idx}] 拉取详情失败 {video_id}: {e}")
            continue

        if video is None:
            logger.warning(f"[{idx}] 视频数据异常（缺少ID），跳过")
            continue

        skip_reason = monitor._filter_reason(video, raw, now_ts)
        if skip_reason:
            logger.info(f"[{idx}] 跳过 {video['id']}，原因: {skip_reason}")
            continue

        if not video.get("webpage_url"):
            logger.info(f"[{idx}] 跳过 {video['id']}，原因: missing_webpage_url")
            continue

        published_ts = video.get("published_ts")
        if not isinstance(published_ts, int) or published_ts <= 0:
            logger.info(f"[{idx}] 跳过 {video['id']}，原因: missing_published_time")
            continue

        if status:
            logger.info(f"[{idx}] 选择视频 {video['id']}（历史状态={status}，将重跑测试链路）")
        else:
            logger.info(f"[{idx}] 选择视频 {video['id']}（未上传）")
        return video

    return None


def run_single_chain_test(target_channel_id: str, *, scan_limit: int | None = None):
    config = load_config()
    logger = setup_logger(config.log_dir)
    prepare_runtime(config, logger)
    # 测试脚本传入的是“指定频道”，因此需要对目标频道再次做详情探针，避免只验证了配置中的其他频道。
    probe_youtube_access(
        target_channel_id,
        cookies_path=config.youtube.cookies,
        cookies_from_browser=config.youtube.cookies_from_browser,
    )
    logger.info(f"目标频道 YouTube 认证探针校验成功: {target_channel_id}")
    state = StateRepository(config.state_db)

    startup_cutoff_ts = int(time.time())
    scheduler = Scheduler(config, logger, state, startup_cutoff_ts=startup_cutoff_ts)
    channel_cfg = _resolve_channel_config(config, logger, target_channel_id)
    use_scan_limit = scan_limit or max(5, config.monitor_scan_limit)

    logger.info(
        "测试模式启动（单次执行）：沿用主程序初始化，但不使用 startup_cutoff 过滤，"
        "将选择最近一个未上传成功的视频进行链路测试。"
    )

    try:
        video = _pick_latest_unuploaded_video(
            scheduler,
            channel_cfg,
            state,
            logger,
            scan_limit=use_scan_limit,
        )
        if not video:
            logger.warning("未找到可用于测试的候选视频（可能最近都已上传/被过滤）。")
            return

        logger.info(f"开始单次链路测试: {video['id']} | {video.get('title')}")
        scheduler.process_video(video, channel_cfg)
        record = state.get_record(video["id"]) or {}
        logger.info(
            f"测试结束: video_id={video['id']} status={record.get('status')} bvid={record.get('bvid')}"
        )
    except KeyboardInterrupt:
        logger.warning("用户中断测试，已退出。")
    finally:
        state.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="单次测试指定创作者的搬运链路（下载/翻译/上传后退出）")
    parser.add_argument("id", type=str, help="YouTube 频道 ID")
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=None,
        help="扫描最近多少条视频作为候选（默认使用配置 monitor_scan_limit）",
    )
    args = parser.parse_args()
    run_single_chain_test(args.id, scan_limit=args.scan_limit)
