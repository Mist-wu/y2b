from pathlib import Path

from src.config.config import load_config
from src.infra.ai_client import _parse_json_value
from src.service.subtitle import SubtitleCue, SubtitleService


class DummyTranslator:
    pass


def service() -> SubtitleService:
    return SubtitleService(load_config(), DummyTranslator())


def test_parse_vtt_skips_blank_padding_after_timing(tmp_path: Path):
    path = tmp_path / "sample.vtt"
    path.write_text(
        "WEBVTT\n\n"
        "00:00:00.240 --> 00:00:01.020\n"
        "   \n"
        "welcome to\n\n",
        encoding="utf-8",
    )

    cues = service().parse(path)

    assert len(cues) == 1
    assert cues[0].text == "welcome to"
    assert cues[0].start == 0.24
    assert cues[0].end == 1.02


def test_split_youtube_timed_vtt_cue():
    cues = service()._split_timed_vtt_cue(
        0.0,
        1.0,
        "welcome <00:00:00.500>to <00:00:00.800>finance",
    )

    assert [cue.text for cue in cues] == ["welcome", "to", "finance"]
    assert cues[0].start == 0.0
    assert cues[0].end == 0.5


def test_repair_continuation_boundaries_merges_bad_endings():
    svc = service()
    cues = [
        SubtitleCue(0.0, 1.0, "we can import data with"),
        SubtitleCue(1.0, 2.0, "the read CSV function"),
    ]

    repaired = svc._repair_continuation_boundaries(cues)

    assert len(repaired) == 1
    assert repaired[0].text == "we can import data with the read CSV function"


def test_ass_time_and_escape():
    svc = service()

    assert svc._ass_time(65.456) == "0:01:05.46"
    assert svc._ass_escape("a{b}\nc") == "a（b）\\Nc"


def test_parse_json_value_tolerates_fenced_json():
    assert _parse_json_value('```json\n{"translations":["你好"]}\n```') == {"translations": ["你好"]}
