from __future__ import annotations

from pathlib import Path

from src.infra.ffmpeg import burn_ass_subtitle, get_video_resolution


class RenderService:
    def __init__(self, logger=None):
        self.logger = logger

    def get_resolution(self, video_path: str | Path) -> tuple[int, int]:
        return get_video_resolution(video_path)

    def burn_subtitle(self, *, input_video: str | Path, ass_path: str | Path, output_video: str | Path) -> Path:
        return burn_ass_subtitle(
            input_video=input_video,
            ass_path=ass_path,
            output_video=output_video,
            logger=self.logger,
        )
