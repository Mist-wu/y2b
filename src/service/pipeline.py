from __future__ import annotations

import time
from pathlib import Path

from src.bootstrap import ensure_bilibili_ready, ensure_runtime_tools, ensure_youtube_ready
from src.service.downloader import DownloaderService
from src.service.renderer import RenderService
from src.service.subtitle import SubtitleService
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService


class SingleVideoPipeline:
    def __init__(self, config, logger, state):
        self.config = config
        self.logger = logger
        self.state = state
        yt_cfg = config.youtube
        self.downloader = DownloaderService(
            youtube_cookies_path=yt_cfg.cookies,
            youtube_cookies_from_browser=yt_cfg.cookies_from_browser,
            youtube_extractor_args=yt_cfg.extractor_args,
        )
        self.translator = TranslatorService(config, logger)
        self.subtitle = SubtitleService(config, self.translator, logger)
        self.renderer = RenderService(config, logger)
        self.uploader = UploaderService(config)

    def run(
        self,
        url: str,
        *,
        job_id: str | None = None,
        source_lang: str | None = None,
        target_lang: str | None = None,
        title_override: str | None = None,
        tags: list[str] | None = None,
        tid: int | None = None,
        no_upload: bool = False,
        keep_files: bool = False,
    ) -> dict:
        job_id = job_id or self.state.create_job(url=url)
        source_lang = source_lang or self.config.translation.source_lang
        target_lang = target_lang or self.config.translation.target_lang
        work_dir: Path | None = None
        downloaded_video: Path | None = None
        started = time.time()

        try:
            self._step(job_id, "checking", 5, "检查运行环境")
            ensure_runtime_tools(self.config, self.logger)
            ensure_youtube_ready(self.config)
            if not no_upload:
                ensure_bilibili_ready(self.config)

            self._step(job_id, "fetching_metadata", 10, "拉取 YouTube 视频信息")
            meta = self.downloader.fetch_metadata(url)
            video_id = str(meta.get("id") or job_id)
            webpage_url = meta.get("webpage_url") or url
            original_title = meta.get("title") or video_id
            self.state.update_job(job_id, video_id=video_id, title=original_title)

            work_dir = Path(self.config.download_dir) / video_id
            work_dir.mkdir(parents=True, exist_ok=True)
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            self._step(job_id, "downloading_video", 20, "下载 YouTube 视频")
            downloaded_video = self.downloader.download_url(webpage_url, work_dir, video_id=video_id, logger=self.logger)
            self.state.update_job(job_id, video_path=str(downloaded_video))

            self._step(job_id, "downloading_subtitle", 35, f"下载 {source_lang} 字幕")
            raw_subtitle = self.downloader.download_subtitle(
                webpage_url,
                work_dir,
                video_id=video_id,
                source_lang=source_lang,
                logger=self.logger,
            )
            self.state.update_job(job_id, subtitle_path=str(raw_subtitle))

            self._step(job_id, "translating_subtitle", 50, f"字幕 {source_lang} -> {target_lang}")
            cues = self.subtitle.parse(raw_subtitle)
            if not cues:
                raise RuntimeError("字幕解析结果为空")
            cues = self.subtitle.translate_cues(cues, source_lang=source_lang, target_lang=target_lang)

            self._step(job_id, "rendering_subtitle", 70, "生成双语 ASS 字幕并压制")
            width, height = self.renderer.get_resolution(downloaded_video)
            ass_path = work_dir / f"{video_id}.bilingual.ass"
            self.subtitle.write_bilingual_ass(cues, ass_path, width=width, height=height)
            rendered_path = output_dir / f"{video_id}.bilingual.mp4"
            self.renderer.burn_subtitle(input_video=downloaded_video, ass_path=ass_path, output_video=rendered_path)
            self.state.update_job(job_id, subtitle_path=str(ass_path), rendered_path=str(rendered_path))

            self._step(job_id, "translating_title", 82, "生成 Bilibili 标题")
            final_title = title_override or self.translator.translate_title(original_title, self.config.bilibili.title_prefix)
            self.state.update_job(job_id, translated_title=final_title)

            if no_upload:
                self._step(job_id, "uploaded", 100, "已完成（未上传）")
                self.state.update_job(job_id, status="completed", progress=100, current_step="已完成（未上传）")
            else:
                self._step(job_id, "uploading", 88, "上传到 Bilibili")
                upload_video = {
                    "id": video_id,
                    "title": original_title,
                    "webpage_url": webpage_url,
                    "channel": meta.get("channel"),
                    "uploader": meta.get("uploader"),
                    "channel_id": meta.get("channel_id"),
                    "description": meta.get("description"),
                    "upload_date": meta.get("upload_date"),
                    "timestamp": meta.get("timestamp"),
                }
                final_tags, final_tid = self._resolve_upload_metadata(
                    original_title=original_title,
                    final_title=final_title,
                    webpage_url=webpage_url,
                    meta=meta,
                    cues=cues,
                    tags=tags,
                    tid=tid,
                )
                bvid = self.uploader.upload(rendered_path, final_title, upload_video, tags=final_tags, tid=final_tid)
                self.state.update_job(job_id, status="uploaded", progress=100, current_step="上传完成", bvid=bvid)

            record = self.state.get_job(job_id) or {}
            self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
            return record

        except KeyboardInterrupt:
            self.state.mark_job_failed(job_id, "用户手动中断")
            raise
        except Exception as e:
            self.state.mark_job_failed(job_id, str(e))
            self.logger.error(f"任务失败 job_id={job_id}: {e}")
            raise
        finally:
            if not keep_files and work_dir and work_dir.exists():
                self._cleanup_workdir(work_dir, preserve_suffixes={".ass"})

    def _resolve_upload_metadata(
        self,
        *,
        original_title: str,
        final_title: str,
        webpage_url: str,
        meta: dict,
        cues: list,
        tags: list[str] | None,
        tid: int | None,
    ) -> tuple[list[str] | None, int | None]:
        final_tags = tags
        final_tid = tid
        if not self.config.bilibili.auto_metadata or (final_tags is not None and final_tid is not None):
            return final_tags, final_tid

        sample = []
        for cue in cues[:12]:
            sample.append({"en": cue.text, "zh": cue.translation or ""})
        payload = {
            "title": original_title,
            "translated_title": final_title,
            "url": webpage_url,
            "uploader": meta.get("channel") or meta.get("uploader") or meta.get("channel_id") or "",
            "description": (meta.get("description") or "")[:1000],
            "subtitle_sample": sample,
        }
        try:
            suggested = self.translator.suggest_bilibili_metadata(payload)
            if final_tags is None:
                final_tags = suggested.get("tags")  # type: ignore[assignment]
            if final_tid is None:
                final_tid = suggested.get("tid")  # type: ignore[assignment]
            self.logger.info(
                f"AI 推荐 Bilibili 元数据: tid={final_tid}({suggested.get('tid_name') or ''}), "
                f"tags={final_tags}"
            )
        except Exception as e:
            self.logger.warning(f"AI 推荐 Bilibili 元数据失败，使用配置默认值: {e}")
        return final_tags, final_tid

    def _step(self, job_id: str, status: str, progress: int, step: str) -> None:
        self.logger.info(f"[{job_id}] {step}")
        self.state.update_job(job_id, status=status, progress=progress, current_step=step, error=None)

    def _cleanup_workdir(self, work_dir: Path, *, preserve_suffixes: set[str]) -> None:
        try:
            for path in work_dir.iterdir():
                if path.suffix in preserve_suffixes:
                    continue
                if path.is_file():
                    path.unlink(missing_ok=True)
            # Remove dir only if empty.
            try:
                work_dir.rmdir()
            except OSError:
                pass
        except Exception as e:
            self.logger.warning(f"清理临时目录失败 {work_dir}: {e}")
