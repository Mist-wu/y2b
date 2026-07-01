from pathlib import Path

import pytest

from src.config.config import load_config
from src.service.pipeline import SingleVideoPipeline
from src.service.subtitle import SubtitleCue
from src.state import StateRepository


class Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class FakeDownloader:
    def __init__(self, work_dir: Path, calls: list[str], *, subtitle_ok: bool = True):
        self.work_dir = work_dir
        self.calls = calls
        self.subtitle_ok = subtitle_ok

    def fetch_metadata(self, _url):
        self.calls.append("metadata")
        return {"id": "video1", "title": "Original", "webpage_url": "https://youtu.be/video1"}

    def download_subtitle(self, _url, _base_dir, *, video_id, source_lang, logger=None):
        self.calls.append("subtitle")
        if not self.subtitle_ok:
            raise RuntimeError("no subtitle")
        path = self.work_dir / f"{video_id}.{source_lang}.vtt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("WEBVTT", encoding="utf-8")
        return path

    def download_url(self, _url, _base_dir, *, video_id, logger=None):
        self.calls.append("video")
        path = self.work_dir / f"{video_id}.mp4"
        path.write_bytes(b"video")
        return path


class FakeSubtitle:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def parse(self, _path):
        return [SubtitleCue(0, 1, "Hello")]

    def segment_cues(self, cues, **_kwargs):
        self.calls.append("segment")
        return cues

    def translate_segmented_cues(self, cues, **_kwargs):
        self.calls.append("translate_subtitle")
        cues[0].translation = "你好"
        return cues

    def save_cues(self, cues, path):
        Path(path).write_text("cached", encoding="utf-8")

    def load_cues(self, _path):
        self.calls.append("load_cache")
        return [SubtitleCue(0, 1, "Hello", "你好")]

    def write_bilingual_ass(self, _cues, path, **_kwargs):
        self.calls.append("ass")
        Path(path).write_text("ass", encoding="utf-8")


class FakeTranslator:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def translate_title(self, *_args, **_kwargs):
        self.calls.append("title")
        return "标题"


class FakeRenderer:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def get_resolution(self, _path):
        return (1920, 1080)

    def burn_subtitle(self, *, output_video, profile=None, **_kwargs):
        self.calls.append(f"render:{profile}")
        Path(output_video).write_bytes(b"rendered")


class FakeUploader:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def upload(self, *_args, **_kwargs):
        self.calls.append("upload")
        return "BV123"


def pipeline(tmp_path, monkeypatch, calls, *, subtitle_ok=True):
    monkeypatch.setattr("src.service.pipeline.ensure_pipeline_tools", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("src.service.pipeline.ensure_youtube_ready", lambda *_args: None)
    monkeypatch.setattr("src.service.pipeline.ensure_bilibili_ready", lambda *_args: None)
    cfg = load_config()
    cfg.download_dir = str(tmp_path / "downloads")
    cfg.output_dir = str(tmp_path / "output")
    repo = StateRepository(str(tmp_path / "state.db"))
    job_id = repo.create_job(url="https://youtu.be/video1")
    pipe = SingleVideoPipeline(cfg, Logger(), repo)
    work_dir = Path(cfg.download_dir) / "video1"
    pipe.downloader = FakeDownloader(work_dir, calls, subtitle_ok=subtitle_ok)
    pipe.subtitle = FakeSubtitle(calls)
    pipe.translator = FakeTranslator(calls)
    pipe.renderer = FakeRenderer(calls)
    pipe.uploader = FakeUploader(calls)
    return pipe, repo, job_id, work_dir


def test_pipeline_checks_subtitle_before_downloading_video(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls, subtitle_ok=False)

    with pytest.raises(RuntimeError, match="no subtitle"):
        pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True)

    assert calls == ["metadata", "subtitle"]
    repo.close()


def test_default_pipeline_still_renders_and_uploads(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    record = pipe.run("https://youtu.be/video1", job_id=job_id, keep_files=True)

    assert record["status"] == "uploaded"
    assert record["bvid"] == "BV123"
    assert "render:None" in calls
    assert "title" in calls
    assert "upload" in calls
    repo.close()


def test_no_upload_skips_title_generation_and_uses_render_profile(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    record = pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True, keep_files=True, render_profile="fast")

    assert record["status"] == "completed"
    assert calls.index("subtitle") < calls.index("video")
    assert "title" not in calls
    assert "render:fast" in calls
    repo.close()


def test_resume_reuses_valid_stage_outputs(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, work_dir = pipeline(tmp_path, monkeypatch, calls)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "video1.en.vtt").write_text("cached subtitle", encoding="utf-8")
    (work_dir / "video1.mp4").write_bytes(b"cached video")
    (work_dir / "video1.en-zh-CN.translated.json").write_text("cached", encoding="utf-8")
    ass_path = work_dir / "video1.bilingual.ass"
    ass_path.write_text("ass", encoding="utf-8")
    output = Path(pipe.config.output_dir) / "video1.bilingual.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"rendered")
    pipe._write_render_manifest(
        output.parent / "video1.bilingual.render.json",
        ass_path,
        work_dir / "video1.mp4",
        pipe.config.render.profile,
    )

    pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True, resume=True, keep_files=True)

    assert calls == ["metadata", "load_cache", "ass"]
    repo.close()


def test_stop_after_ass_skips_video_render_and_upload(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    record = pipe.run("https://youtu.be/video1", job_id=job_id, keep_files=True, stop_after="ass")

    assert record["status"] == "completed"
    assert record["rendered_path"] is None
    assert Path(record["subtitle_path"]).name == "video1.bilingual.ass"
    assert calls == ["metadata", "subtitle", "segment", "translate_subtitle", "ass"]
    repo.close()


def test_stop_after_translation_skips_video_and_ass(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    record = pipe.run("https://youtu.be/video1", job_id=job_id, keep_files=True, stop_after="translation")

    assert record["status"] == "completed"
    assert record["subtitle_path"].endswith("video1.en-zh-CN.translated.json")
    assert record["rendered_path"] is None
    assert calls == ["metadata", "subtitle", "segment", "translate_subtitle"]
    repo.close()


def test_stop_after_subtitle_skips_parsing_and_translation(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    record = pipe.run("https://youtu.be/video1", job_id=job_id, keep_files=True, stop_after="subtitle")

    assert record["status"] == "completed"
    assert record["subtitle_path"].endswith("video1.en.vtt")
    assert record["rendered_path"] is None
    assert calls == ["metadata", "subtitle"]
    repo.close()


def test_no_upload_cannot_target_upload(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, _work_dir = pipeline(tmp_path, monkeypatch, calls)

    with pytest.raises(RuntimeError, match="--no-upload"):
        pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True, stop_after="upload")

    repo.close()


def test_resume_continues_from_segmented_cache(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, work_dir = pipeline(tmp_path, monkeypatch, calls)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "video1.en.vtt").write_text("cached subtitle", encoding="utf-8")
    (work_dir / "video1.mp4").write_bytes(b"cached video")
    (work_dir / "video1.en.segmented.json").write_text("cached", encoding="utf-8")

    pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True, resume=True, keep_files=True)

    assert "load_cache" in calls
    assert "segment" not in calls
    assert "translate_subtitle" in calls
    repo.close()


def test_resume_rerenders_when_translation_changes_ass(tmp_path, monkeypatch):
    calls = []
    pipe, repo, job_id, work_dir = pipeline(tmp_path, monkeypatch, calls)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "video1.en.vtt").write_text("cached subtitle", encoding="utf-8")
    input_video = work_dir / "video1.mp4"
    input_video.write_bytes(b"cached video")
    (work_dir / "video1.en.segmented.json").write_text("cached", encoding="utf-8")
    old_ass = work_dir / "video1.bilingual.ass"
    old_ass.write_text("old ass", encoding="utf-8")
    output = Path(pipe.config.output_dir) / "video1.bilingual.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"rendered")
    pipe._write_render_manifest(
        output.parent / "video1.bilingual.render.json",
        old_ass,
        input_video,
        pipe.config.render.profile,
    )

    pipe.run("https://youtu.be/video1", job_id=job_id, no_upload=True, resume=True, keep_files=True)

    assert "translate_subtitle" in calls
    assert "ass" in calls
    assert "render:None" in calls
    repo.close()
