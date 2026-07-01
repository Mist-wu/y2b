from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from src.bootstrap import ensure_bilibili_ready, ensure_pipeline_tools, ensure_youtube_ready
from src.service.downloader import DownloaderService
from src.service.renderer import RenderService
from src.service.subtitle import SubtitleService
from src.service.translator import TranslatorService
from src.service.uploader import UploaderService


@dataclass
class RunContext:
    """Shared, read-only state threaded through every pipeline stage for one job run."""

    job_id: str
    video_id: str
    webpage_url: str
    original_title: str
    meta: dict
    work_dir: Path
    output_dir: Path


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

    _STAGE_ORDER = {
        "subtitle": 1,
        "translation": 2,
        "ass": 3,
        "render": 4,
        "upload": 5,
    }

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
        stop_after: str | None = None,
    ) -> dict:
        job_id = job_id or self.state.create_job(url=url)
        source_lang = source_lang or self.config.translation.source_lang
        target_lang = target_lang or self.config.translation.target_lang
        target_stage = self._resolve_target_stage(no_upload=no_upload, stop_after=stop_after)
        work_dir: Path | None = None
        started = time.time()
        succeeded = False
        cleanup_preserve_suffixes: set[str] = {".ass"}

        try:
            self._step(job_id, "checking", 5, "检查运行环境")
            ensure_pipeline_tools(
                self.config,
                self.logger,
                needs_render=self._reaches_stage(target_stage, "render"),
                needs_upload=target_stage == "upload",
            )
            ensure_youtube_ready(self.config)
            if target_stage == "upload":
                ensure_bilibili_ready(self.config)

            meta = self._fetch_metadata_stage(job_id, url)
            work_dir = Path(self.config.download_dir) / str(meta["video_id"])
            work_dir.mkdir(parents=True, exist_ok=True)
            output_dir = Path(self.config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            ctx = RunContext(
                job_id=job_id,
                video_id=str(meta["video_id"]),
                webpage_url=str(meta["webpage_url"]),
                original_title=str(meta["title"]),
                meta=meta,
                work_dir=work_dir,
                output_dir=output_dir,
            )

            raw_subtitle = self._download_subtitle_stage(ctx, source_lang=source_lang, resume=resume)
            if target_stage == "subtitle":
                cleanup_preserve_suffixes = {raw_subtitle.suffix}
                record = self._complete_job(
                    job_id,
                    current_step="已完成（仅下载字幕）",
                    subtitle_path=str(raw_subtitle),
                    rendered_path=None,
                )
                succeeded = True
                self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
                return record

            cues = self.subtitle.parse(raw_subtitle)
            if not cues:
                raise RuntimeError("字幕解析结果为空")

            downloaded_video: Path | None = None
            if self._reaches_stage(target_stage, "render"):
                downloaded_video = self._download_video_stage(ctx, resume=resume)

            cues, translated_cache_path = self._translate_subtitle_stage(
                ctx,
                cues,
                source_lang=source_lang,
                target_lang=target_lang,
                resume=resume,
            )
            if target_stage == "translation":
                cleanup_preserve_suffixes = {".json"}
                record = self._complete_job(
                    job_id,
                    current_step="已完成（仅翻译字幕）",
                    subtitle_path=str(translated_cache_path),
                    rendered_path=None,
                )
                succeeded = True
                self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
                return record

            ass_path = self._write_ass_stage(
                ctx,
                cues,
                downloaded_video=downloaded_video,
                reaches_render=self._reaches_stage(target_stage, "render"),
            )
            if target_stage == "ass":
                cleanup_preserve_suffixes = {".ass"}
                record = self._complete_job(
                    job_id,
                    current_step="已完成（仅生成双语 ASS，未压制/未上传）",
                    subtitle_path=str(ass_path),
                    rendered_path=None,
                )
                succeeded = True
                self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
                return record

            if downloaded_video is None:
                raise RuntimeError("内部错误：压制阶段缺少输入视频")
            rendered_path = self._render_stage(
                ctx,
                ass_path,
                downloaded_video,
                render_profile=render_profile,
                resume=resume,
            )

            if target_stage == "render":
                message = "已完成（未上传）" if no_upload else "已完成（压制完成，未上传）"
                record = self._complete_job(
                    job_id,
                    current_step=message,
                    subtitle_path=str(ass_path),
                    rendered_path=str(rendered_path),
                )
                succeeded = True
                self.logger.info(f"任务完成 job_id={job_id} 耗时={time.time() - started:.1f}s")
                return record

            self._upload_stage(
                ctx,
                rendered_path,
                cues=cues,
                title_override=title_override,
                tags=tags,
                tid=tid,
            )

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
                self._cleanup_workdir(work_dir, preserve_suffixes=cleanup_preserve_suffixes)

    def _resolve_target_stage(self, *, no_upload: bool, stop_after: str | None) -> str:
        target = stop_after or ("render" if no_upload else "upload")
        if target not in self._STAGE_ORDER:
            choices = ", ".join(self._STAGE_ORDER)
            raise RuntimeError(f"未知流程阶段: {target}（可选: {choices}）")
        if no_upload and target == "upload":
            raise RuntimeError("--no-upload 与 --stop-after upload 不能同时使用")
        return target

    def _reaches_stage(self, target_stage: str, stage: str) -> bool:
        return self._STAGE_ORDER[target_stage] >= self._STAGE_ORDER[stage]

    def _fetch_metadata_stage(self, job_id: str, url: str) -> dict:
        self._step(job_id, "fetching_metadata", 10, "拉取 YouTube 视频信息")
        meta = self.downloader.fetch_metadata(url)
        video_id = str(meta.get("id") or job_id)
        webpage_url = meta.get("webpage_url") or url
        original_title = meta.get("title") or video_id
        self.state.update_job(job_id, video_id=video_id, title=original_title)
        return {**meta, "video_id": video_id, "webpage_url": webpage_url, "title": original_title}

    def _download_subtitle_stage(self, ctx: RunContext, *, source_lang: str, resume: bool) -> Path:
        self._step(ctx.job_id, "downloading_subtitle", 20, f"下载 {source_lang} 字幕")
        raw_subtitle = self._find_existing_subtitle(ctx.work_dir, ctx.video_id, source_lang) if resume else None
        if raw_subtitle:
            self.logger.info(f"恢复任务：复用字幕文件 {raw_subtitle}")
        else:
            raw_subtitle = self.downloader.download_subtitle(
                ctx.webpage_url,
                ctx.work_dir,
                video_id=ctx.video_id,
                source_lang=source_lang,
                logger=self.logger,
            )
        self.state.update_job(ctx.job_id, subtitle_path=str(raw_subtitle))
        return raw_subtitle

    def _download_video_stage(self, ctx: RunContext, *, resume: bool) -> Path:
        self._step(ctx.job_id, "downloading_video", 35, "下载 YouTube 视频")
        expected_video = ctx.work_dir / f"{ctx.video_id}.mp4"
        if resume and self._can_reuse_video(expected_video):
            downloaded_video = expected_video
            self.logger.info(f"恢复任务：复用视频文件 {downloaded_video}")
        else:
            downloaded_video = self.downloader.download_url(
                ctx.webpage_url, ctx.work_dir, video_id=ctx.video_id, logger=self.logger
            )
        self.state.update_job(ctx.job_id, video_path=str(downloaded_video))
        return downloaded_video

    def _translate_subtitle_stage(
        self,
        ctx: RunContext,
        cues: list,
        *,
        source_lang: str,
        target_lang: str,
        resume: bool,
    ) -> tuple[list, Path]:
        self._step(ctx.job_id, "translating_subtitle", 50, f"字幕 {source_lang} -> {target_lang}")
        segmented_cache_path = self._segmented_cache_path(ctx.work_dir, ctx.video_id, source_lang)
        cache_path = self._translated_cache_path(ctx.work_dir, ctx.video_id, source_lang, target_lang)
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
        return cues, cache_path

    def _write_ass_stage(
        self,
        ctx: RunContext,
        cues: list,
        *,
        downloaded_video: Path | None,
        reaches_render: bool,
    ) -> Path:
        step = "生成双语 ASS 字幕并压制" if reaches_render else "生成双语 ASS 字幕"
        self._step(ctx.job_id, "rendering_subtitle", 70, step)
        ass_path = ctx.work_dir / f"{ctx.video_id}.bilingual.ass"
        if downloaded_video is not None:
            width, height = self.renderer.get_resolution(downloaded_video)
        else:
            width, height = self._metadata_resolution(ctx.meta)
        self.subtitle.write_bilingual_ass(cues, ass_path, width=width, height=height)
        self.state.update_job(ctx.job_id, subtitle_path=str(ass_path))
        return ass_path

    def _render_stage(
        self,
        ctx: RunContext,
        ass_path: Path,
        downloaded_video: Path,
        *,
        render_profile: str | None,
        resume: bool,
    ) -> Path:
        rendered_path = ctx.output_dir / f"{ctx.video_id}.bilingual.mp4"
        render_manifest_path = ctx.output_dir / f"{ctx.video_id}.bilingual.render.json"
        render_profile_name = render_profile or self.config.render.profile
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
        self.state.update_job(ctx.job_id, subtitle_path=str(ass_path), rendered_path=str(rendered_path))
        return rendered_path

    def _upload_stage(
        self,
        ctx: RunContext,
        rendered_path: Path,
        *,
        cues: list,
        title_override: str | None,
        tags: list[str] | None,
        tid: int | None,
    ) -> None:
        self._step(ctx.job_id, "translating_title", 82, "生成 Bilibili 标题")
        final_title = title_override or self.translator.translate_title(
            ctx.original_title, self.config.bilibili.title_prefix
        )
        self.state.update_job(ctx.job_id, translated_title=final_title)
        self._step(ctx.job_id, "uploading", 88, "上传到 Bilibili")
        cover_path: Path | None = None
        try:
            cover_path = self.downloader.download_thumbnail(
                ctx.meta, ctx.work_dir, video_id=ctx.video_id, logger=self.logger
            )
        except Exception as e:
            self.logger.warning(f"下载 YouTube 封面失败，将使用 Bilibili 默认封面: {e}")
        upload_video = {
            "id": ctx.video_id,
            "title": ctx.original_title,
            "webpage_url": ctx.webpage_url,
            "channel": ctx.meta.get("channel"),
            "uploader": ctx.meta.get("uploader"),
            "channel_id": ctx.meta.get("channel_id"),
            "description": ctx.meta.get("description"),
            "upload_date": ctx.meta.get("upload_date"),
            "timestamp": ctx.meta.get("timestamp"),
        }
        final_tags, final_tid = self._resolve_upload_metadata(
            original_title=ctx.original_title,
            final_title=final_title,
            webpage_url=ctx.webpage_url,
            meta=ctx.meta,
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
        self.state.update_job(ctx.job_id, status="uploaded", progress=100, current_step="上传完成", bvid=bvid)

    def _complete_job(self, job_id: str, *, current_step: str, **fields) -> dict:
        self.state.update_job(job_id, status="completed", progress=100, current_step=current_step, **fields)
        return self.state.get_job(job_id) or {}

    def _metadata_resolution(self, meta: dict) -> tuple[int, int]:
        def pair(item: dict) -> tuple[int, int] | None:
            try:
                width = int(item.get("width") or 0)
                height = int(item.get("height") or 0)
            except Exception:
                return None
            if width > 0 and height > 0:
                return width, height
            return None

        direct = pair(meta)
        if direct:
            return direct
        candidates: list[tuple[int, int]] = []
        for fmt in meta.get("formats") or []:
            if not isinstance(fmt, dict) or fmt.get("vcodec") == "none":
                continue
            candidate = pair(fmt)
            if candidate:
                candidates.append(candidate)
        if candidates:
            return max(candidates, key=lambda size: size[0] * size[1])
        return 1920, 1080

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
