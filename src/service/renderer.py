from __future__ import annotations

from pathlib import Path

from src.infra.ffmpeg import burn_ass_subtitle, get_video_resolution


class RenderService:
    def __init__(self, config=None, logger=None):
        self.config = config
        self.logger = logger

    def get_resolution(self, video_path: str | Path) -> tuple[int, int]:
        return get_video_resolution(video_path)

    def burn_subtitle(
        self,
        *,
        input_video: str | Path,
        ass_path: str | Path,
        output_video: str | Path,
        profile: str | None = None,
    ) -> Path:
        render_cfg = getattr(self.config, "render", None)
        selected = profile or getattr(render_cfg, "profile", "quality")
        encoding = getattr(render_cfg, selected, None)
        return burn_ass_subtitle(
            input_video=input_video,
            ass_path=ass_path,
            output_video=output_video,
            fonts_dir=getattr(getattr(self.config, "subtitle_style", None), "fonts_dir", None),
            logger=self.logger,
            codec=getattr(encoding, "codec", "libx264"),
            preset=getattr(encoding, "preset", "medium"),
            crf=getattr(encoding, "crf", 20),
            bitrate=getattr(encoding, "bitrate", None),
        )
