from __future__ import annotations

import hashlib
import json
import re
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
            max_retry=config.max_retry,
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
        resume: bool = False,
        render_profile: str | None = None,
    ) -> dict:
        job_id = job_id or self.state.create_job(url=url)
        source_lang = source_lang or self.config.translation.source_lang
        target_lang = target_lang or self.config.translation.target_lang
        work_dir: Path | None = None
        downloaded_video: Path | None = None
        started = time.time()
        succeeded = False

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

            self._step(job_id, "downloading_subtitle", 20, f"下载 {source_lang} 字幕")
            raw_subtitle = self._find_existing_subtitle(work_dir, video_id, source_lang) if resume else None
            if raw_subtitle:
                self.logger.info(f"恢复任务：复用字幕文件 {raw_subtitle}")
            else:
                raw_subtitle = self.downloader.download_subtitle(
                    webpage_url,
                    work_dir,
                    video_id=video_id,
                    source_lang=source_lang,
                    logger=self.logger,
                )
            self.state.update_job(job_id, subtitle_path=str(raw_subtitle))

            cues = self.subtitle.parse(raw_subtitle)
            if not cues:
                raise RuntimeError("字幕解析结果为空")

            self._step(job_id, "downloading_video", 35, "下载 YouTube 视频")
            expected_video = work_dir / f"{video_id}.mp4"
            if resume and self._can_reuse_video(expected_video):
                downloaded_video = expected_video
                self.logger.info(f"恢复任务：复用视频文件 {downloaded_video}")
            else:
                downloaded_video = self.downloader.download_url(webpage_url, work_dir, video_id=video_id, logger=self.logger)
            self.state.update_job(job_id, video_path=str(downloaded_video))

            self._step(job_id, "translating_subtitle", 50, f"字幕 {source_lang} -> {target_lang}")
            segmented_cache_path = self._segmented_cache_path(work_dir, video_id, source_lang)
            cache_path = self._translated_cache_path(work_dir, video_id, source_lang, target_lang)
            if resume and cache_path.exists():
                try:
                    cues = self.subtitle.load_cues(cache_path)
                    self.logger.info(f"恢复任务：复用字幕翻译缓存 {cache_path}")
                except Exception as e:
                    self.logger.warning(f"字幕翻译缓存不可用，将重新翻译: {e}")
                    cues = self._segment_and_translate(
                        cues, segmented_cache_path, cache_path, source_lang, target_lang, resume=resume
                    )
            else:
                cues = self._segment_and_translate(
                    cues, segmented_cache_path, cache_path, source_lang, target_lang, resume=resume
                )

            self._step(job_id, "rendering_subtitle", 70, "生成双语 ASS 字幕并压制")
            ass_path = work_dir / f"{video_id}.bilingual.ass"
            rendered_path = output_dir / f"{video_id}.bilingual.mp4"
            render_manifest_path = output_dir / f"{video_id}.bilingual.render.json"
            render_profile_name = render_profile or self.config.render.profile
            width, height = self.renderer.get_resolution(downloaded_video)
            self.subtitle.write_bilingual_ass(cues, ass_path, width=width, height=height)
            if resume and self._can_reuse_rendered_output(
                rendered_path,
                render_manifest_path,
                ass_path,
                downloaded_video,
                render_profile_name,
            ):
                self.logger.info(f"恢复任务：复用已压制视频 {rendered_path}")
            else:
                self.renderer.burn_subtitle(
                    input_video=downloaded_video,
                    ass_path=ass_path,
                    output_video=rendered_path,
                    profile=render_profile,
                )
                self._write_render_manifest(render_manifest_path, ass_path, downloaded_video, render_profile_name)
            self.state.update_job(job_id, subtitle_path=str(ass_path), rendered_path=str(rendered_path))

            if no_upload:
                self.state.update_job(job_id, status="completed", progress=100, current_step="已完成（未上传）")
            else:
                self._step(job_id, "translating_title", 82, "生成 Bilibili 标题")
                final_title = title_override or self.translator.translate_title(original_title, self.config.bilibili.title_prefix)
                self.state.update_job(job_id, translated_title=final_title)
                self._step(job_id, "uploading", 88, "上传到 Bilibili")
                cover_path: Path | None = None
                try:
                    cover_path = self.downloader.download_thumbnail(meta, work_dir, video_id=video_id, logger=self.logger)
                except Exception as e:
                    self.logger.warning(f"下载 YouTube 封面失败，将使用 Bilibili 默认封面: {e}")
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
                bvid = self.uploader.upload(
                    rendered_path,
                    final_title,
                    upload_video,
                    tags=final_tags,
                    tid=final_tid,
                    cover_path=cover_path,
                )
                self.state.update_job(job_id, status="uploaded", progress=100, current_step="上传完成", bvid=bvid)

            record = self.state.get_job(job_id) or {}
            self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
            succeeded = True
            return record

        except KeyboardInterrupt:
            self.state.mark_job_failed(job_id, "用户手动中断")
            raise
        except Exception as e:
            self.state.mark_job_failed(job_id, str(e))
            self.logger.error(f"任务失败 job_id={job_id}: {e}")
            raise
        finally:
            if succeeded and not keep_files and work_dir and work_dir.exists():
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

    def _find_existing_subtitle(self, work_dir: Path, video_id: str, source_lang: str) -> Path | None:
        candidates = [
            *work_dir.glob(f"{video_id}.{source_lang}*.vtt"),
            *work_dir.glob(f"{video_id}.{source_lang}*.srt"),
        ]
        return next((path for path in sorted(candidates) if path.stat().st_size > 0), None)

    def _translated_cache_path(self, work_dir: Path, video_id: str, source_lang: str, target_lang: str) -> Path:
        lang_key = re.sub(r"[^A-Za-z0-9_-]", "_", f"{source_lang}-{target_lang}")
        return work_dir / f"{video_id}.{lang_key}.translated.json"

    def _segmented_cache_path(self, work_dir: Path, video_id: str, source_lang: str) -> Path:
        lang_key = re.sub(r"[^A-Za-z0-9_-]", "_", source_lang)
        return work_dir / f"{video_id}.{lang_key}.segmented.json"

    def _segment_and_translate(
        self,
        cues: list,
        segmented_cache_path: Path,
        translated_cache_path: Path,
        source_lang: str,
        target_lang: str,
        *,
        resume: bool,
    ) -> list:
        if resume and segmented_cache_path.exists():
            try:
                cues = self.subtitle.load_cues(segmented_cache_path)
                self.logger.info(f"恢复任务：复用智能分句缓存 {segmented_cache_path}")
            except Exception as e:
                self.logger.warning(f"智能分句缓存不可用，将重新分句: {e}")
                cues = self.subtitle.segment_cues(cues, source_lang=source_lang)
                self.subtitle.save_cues(cues, segmented_cache_path)
        else:
            cues = self.subtitle.segment_cues(cues, source_lang=source_lang)
            self.subtitle.save_cues(cues, segmented_cache_path)
        cues = self.subtitle.translate_segmented_cues(cues, source_lang=source_lang, target_lang=target_lang)
        self.subtitle.save_cues(cues, translated_cache_path)
        return cues

    def _can_reuse_video(self, path: Path) -> bool:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        try:
            self.renderer.get_resolution(path)
            return True
        except Exception as e:
            self.logger.warning(f"恢复任务：已有视频无法校验，将重新生成 {path}: {e}")
            return False

    def _can_reuse_rendered_output(
        self,
        rendered_path: Path,
        manifest_path: Path,
        ass_path: Path,
        input_video: Path,
        profile_name: str,
    ) -> bool:
        if not self._can_reuse_video(rendered_path) or not manifest_path.exists():
            return False
        try:
            expected = self._render_manifest_payload(ass_path, input_video, profile_name)
            actual = json.loads(manifest_path.read_text(encoding="utf-8"))
            return actual == expected
        except Exception as e:
            self.logger.warning(f"恢复任务：压制缓存校验失败，将重新压制: {e}")
            return False

    def _write_render_manifest(self, path: Path, ass_path: Path, input_video: Path, profile_name: str) -> None:
        path.write_text(
            json.dumps(self._render_manifest_payload(ass_path, input_video, profile_name), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _render_manifest_payload(self, ass_path: Path, input_video: Path, profile_name: str) -> dict:
        video_stat = input_video.stat()
        profile = getattr(self.config.render, profile_name).model_dump(mode="json")
        return {
            "ass_sha256": hashlib.sha256(ass_path.read_bytes()).hexdigest(),
            "input_video": str(input_video.resolve()),
            "input_size": video_stat.st_size,
            "input_mtime_ns": video_stat.st_mtime_ns,
            "profile_name": profile_name,
            "profile": profile,
        }

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
