from src.config.config import load_config
from src.infra.ffmpeg import burn_ass_subtitle
from src.service.renderer import RenderService


def test_render_service_passes_fast_profile_to_ffmpeg(monkeypatch, tmp_path):
    captured = {}

    def fake_burn(**kwargs):
        captured.update(kwargs)
        return tmp_path / "output.mp4"

    monkeypatch.setattr("src.service.renderer.burn_ass_subtitle", fake_burn)

    RenderService(load_config()).burn_subtitle(
        input_video=tmp_path / "in.mp4",
        ass_path=tmp_path / "in.ass",
        output_video=tmp_path / "output.mp4",
        profile="fast",
    )

    assert captured["codec"] == "h264_videotoolbox"
    assert captured["crf"] is None
    assert captured["bitrate"] == "6M"


def test_ffmpeg_fast_encoding_builds_hardware_command(monkeypatch, tmp_path):
    captured = {}

    class Process:
        stdout = []

        def wait(self):
            return 0

    monkeypatch.setattr("src.infra.ffmpeg._bin", lambda _name: "ffmpeg")

    def fake_popen(cmd, **_kwargs):
        captured["cmd"] = cmd
        return Process()

    monkeypatch.setattr("src.infra.ffmpeg.subprocess.Popen", fake_popen)

    burn_ass_subtitle(
        input_video=tmp_path / "input.mp4",
        ass_path=tmp_path / "subtitle.ass",
        output_video=tmp_path / "output.mp4",
        codec="h264_videotoolbox",
        preset=None,
        crf=None,
        bitrate="6M",
    )

    assert "h264_videotoolbox" in captured["cmd"]
    assert "-crf" not in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-b:v") + 1] == "6M"
