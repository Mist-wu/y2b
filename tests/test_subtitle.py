from pathlib import Path

from src.config.config import load_config
from src.infra.ai_client import _parse_json_value, build_subtitle_translation_prompt
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


def test_parse_srt(tmp_path: Path):
    path = tmp_path / "sample.srt"
    path.write_text(
        "1\n"
        "00:00:01,200 --> 00:00:03,500\n"
        "Hello, world!\n\n"
        "2\n"
        "00:00:03,600 --> 00:00:05,800\n"
        "Subtitle two\n",
        encoding="utf-8",
    )

    cues = service().parse(path)

    assert len(cues) == 2
    assert cues[0].text == "Hello, world!"
    assert cues[0].start == 1.2
    assert cues[0].end == 3.5
    assert cues[1].text == "Subtitle two"
    assert cues[1].start == 3.6
    assert cues[1].end == 5.8


def test_close_short_gaps():
    svc = service()
    cues = [
        SubtitleCue(0.0, 1.0, "First"),
        SubtitleCue(1.2, 2.0, "Second"),
    ]
    closed = svc._close_short_gaps(cues)
    assert closed[0].end == 1.2
    assert closed[1].start == 1.2


def test_merge_orphan_short_cue_attaches_trailing_noun():
    svc = service()
    cues = [
        SubtitleCue(86.96, 90.36, "games incredible"),
        SubtitleCue(90.36, 91.91, "story"),
    ]

    merged = svc._merge_orphan_short_cues(cues)

    assert len(merged) == 1
    assert merged[0].text == "games incredible story"
    assert merged[0].end == 91.91


def test_trim_unusually_long_short_final_cue():
    svc = service()
    cues = [SubtitleCue(90.36, 110.35, "story")]

    trimmed = svc._trim_unusually_long_cues(cues)

    assert round(trimmed[0].end, 2) == 91.91


def test_trim_unusually_long_keeps_normal_long_sentence():
    svc = service()
    text = "is just going through engineering of the car itself as well as engineering of the factory"
    cues = [SubtitleCue(119.76, 128.36, text)]

    trimmed = svc._trim_unusually_long_cues(cues)

    assert trimmed[0].end == 128.36


def test_dedupe_repeated_words():
    svc = service()
    # "welcome to finance" repeated (length of block 3 words)
    text = "welcome to finance welcome to finance today"
    deduped = svc._dedupe_repeated_words(text)
    assert deduped == "welcome to finance today"


def test_dedupe_repeated_words_inside_sentence():
    svc = service()
    text = "first we load the CSV load the CSV and then call tail"
    deduped = svc._dedupe_repeated_words(text)
    assert deduped == "first we load the CSV and then call tail"


def test_clean_filler_cues_drops_standalone_fillers_and_strips_edges():
    svc = service()
    cues = [
        SubtitleCue(0.0, 0.6, "um"),
        SubtitleCue(0.6, 2.0, "yeah we import pandas"),
        SubtitleCue(2.1, 2.7, "uh"),
    ]

    cleaned = svc._clean_filler_cues(cues)

    assert len(cleaned) == 1
    assert cleaned[0].text == "we import pandas"
    assert cleaned[0].end == 2.7


def test_subtitle_translation_prompt_targets_teaching_and_game_content():
    prompt = build_subtitle_translation_prompt(load_config().translation)

    assert "编程教程、量化金融教学、荒野乱斗/游戏解说" in prompt
    assert "不要翻成纪录片腔" in prompt
    assert "函数名、API" in prompt


def test_wrap_text_preserves_all_lines():
    svc = service()
    text = "This is a very long text"
    wrapped = svc._wrap_text(text, max_chars=10)

    assert wrapped == "This is a\nvery long\ntext"


def test_wrap_text_counts_chinese_as_double_width():
    svc = service()
    text = "中文教学字幕需要更自然地换行"
    wrapped = svc._wrap_text(text, max_chars=12)

    assert wrapped == "中文教学字幕\n需要更自然地\n换行"


def test_wrap_text_does_not_split_latin_words_inside_chinese():
    svc = service()
    text = "我们还会导入一个NumPy的封装库Pandas，用来存储数据，以及Matplotlib，用于数据可视化。"
    wrapped = svc._wrap_text(text, max_chars=34)

    assert "Matplotli\nb" not in wrapped
    assert "Matplotlib" in wrapped
    assert wrapped.replace("\n", "") == text


def test_wrap_text_does_not_split_english_words_or_truncate():
    svc = service()
    text = "If you're interested in finance data science, you're in the right place"
    wrapped = svc._wrap_text(text, max_chars=24)

    assert "you're" in wrapped
    assert wrapped.replace("\n", " ") == text


def test_subtitle_max_display_width_is_wider_for_2k_video():
    svc = service()

    assert svc._subtitle_max_display_width(2560, 75, language="cjk") == 59
    assert svc._subtitle_max_display_width(2560, 37, language="latin") == 104


def test_bilingual_cn_margin_tracks_actual_english_line_count():
    svc = service()

    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=37, en_line_count=1) == 98
    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=37, en_line_count=2) == 135
    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=37, en_line_count=3) == 171


def test_write_bilingual_ass_uses_per_cue_cn_margin(tmp_path: Path):
    svc = service()
    output = tmp_path / "sample.ass"
    long_en = " ".join(["long"] * 30)
    cues = [
        SubtitleCue(
            0.0,
            1.0,
            "short english",
            "这是一段会换成两行的中文教学字幕，用来验证中文多行时不会被整体抬得太高",
        ),
        SubtitleCue(1.0, 2.0, long_en, "短中文"),
    ]

    svc.write_bilingual_ass(cues, output, width=2560, height=1440)
    dialogue_lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue: 1")]

    assert ",CN,,0,0,98,," in dialogue_lines[0]
    assert r"\N" in dialogue_lines[0]
    assert ",CN,,0,0,135,," in dialogue_lines[1]

