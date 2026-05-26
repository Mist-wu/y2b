from pathlib import Path
import time

from src.config.config import load_config
from src.infra.ai_client import _coerce_translation_result, _parse_json_value, build_subtitle_translation_prompt
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
    assert repaired[0].text == "we can import data with the read_csv function"


def test_repair_continuation_boundaries_merges_speech_continuations():
    svc = service()
    cues = [
        SubtitleCue(45.90, 51.54, "let's download monthly stock prices for the ETF spy adjusted close gives"),
        SubtitleCue(51.54, 54.18, "us closing prices that are inclusive of cash flows so"),
        SubtitleCue(54.18, 57.00, "we aren't leaving any data out this way returns"),
        SubtitleCue(57.00, 61.08, "will be calculated as total returns as opposed to price returns"),
    ]

    repaired = svc._repair_continuation_boundaries(cues)

    assert [cue.text for cue in repaired] == [
        "let's download monthly stock prices for the ETF spy adjusted close gives us closing prices that are inclusive of cash flows so",
        "we aren't leaving any data out this way returns will be calculated as total returns as opposed to price returns",
    ]


def test_repair_continuation_boundaries_does_not_overmerge_common_so():
    svc = service()
    cues = [
        SubtitleCue(0.0, 2.0, "I think so"),
        SubtitleCue(2.0, 4.0, "we should continue with the next example"),
    ]

    repaired = svc._repair_continuation_boundaries(cues)

    assert [cue.text for cue in repaired] == [
        "I think so",
        "we should continue with the next example",
    ]


def test_ass_time_and_escape():
    svc = service()

    assert svc._ass_time(65.456) == "0:01:05.46"
    assert svc._ass_escape("a{b}\nc") == "a（b）\\Nc"


def test_parse_json_value_tolerates_fenced_json():
    assert _parse_json_value('```json\n{"translations":["你好"]}\n```') == {"translations": ["你好"]}


def test_coerce_translation_result_supports_indexed_objects():
    raw = {"translations": [{"i": 1, "text": "第二条"}, {"i": 0, "text": "第一条"}]}

    assert _coerce_translation_result(raw, expected_count=2) == ["第一条", "第二条"]


def test_coerce_translation_result_supports_index_mapping():
    raw = {"translations": {"0": "第一条", "1": ""}}

    assert _coerce_translation_result(raw, expected_count=2) == ["第一条", ""]


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


def test_normalize_asr_terms_is_curated_and_non_destructive():
    svc = service()
    text = "we use y Finance then call Dot Plot and drop n a with the two period method"

    normalized = svc._clean_caption_text(text)

    assert normalized == "we use yfinance then call plot() and dropna with the to_period method"


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

    assert svc._subtitle_max_display_width(2560, 75, language="cjk") == 64
    assert svc._subtitle_max_display_width(2560, 43, language="latin") == 102


def test_bilingual_cn_margin_tracks_actual_english_line_count():
    svc = service()

    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=43, en_line_count=1) == 99
    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=43, en_line_count=2) == 144
    assert svc._bilingual_cn_margin(height=1440, en_margin=48, en_size=43, en_line_count=3) == 189


def test_write_bilingual_ass_keeps_chinese_single_line(tmp_path: Path):
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
    text = output.read_text(encoding="utf-8")
    cn_dialogue_lines = [line for line in text.splitlines() if line.startswith("Dialogue: 1")]

    assert len(cn_dialogue_lines) == 3
    assert all(r"\N" not in line for line in cn_dialogue_lines)
    assert all(r"{\q2}" in line for line in cn_dialogue_lines)
    assert ",CN,,0,0,99,," in cn_dialogue_lines[0]
    assert ",CN,,0,0,144,," in cn_dialogue_lines[-1]


def test_split_cue_for_single_line_cn_splits_translation_and_timing():
    svc = service()
    cue = SubtitleCue(
        0.0,
        4.0,
        "this sentence should be split into parallel chunks for display",
        "这是一条特别长的中文字幕，需要被拆成多条连续字幕来保证始终单行显示",
    )

    split = svc._split_cue_for_single_line_cn(cue, max_chars=28)

    assert len(split) > 1
    assert split[0].start == 0.0
    assert split[-1].end == 4.0
    assert all("\n" not in item.translation for item in split if item.translation)
    assert all(svc._display_width(item.translation or "") <= 28 for item in split)


def test_split_cue_for_short_duration_preserves_long_translation():
    svc = service()
    translation = "这是一个非常非常长的中文字幕内容用于验证短时间字幕被拆分之后不会丢失尾部文字并影响准确性"
    cue = SubtitleCue(0.0, 1.0, "short english caption", translation)

    split = svc._split_cue_for_single_line_cn(cue, max_chars=12)

    assert split == [cue]
    assert "".join(item.translation or "" for item in split) == translation


def test_subtitle_cache_round_trip(tmp_path: Path):
    svc = service()
    path = tmp_path / "translated.json"
    cues = [SubtitleCue(0.0, 1.0, "Hello", "你好")]

    svc.save_cues(cues, path)

    assert svc.load_cues(path) == cues


def test_concurrent_translation_preserves_cue_order():
    completed: list[str] = []

    class ParallelTranslator:
        def translate_subtitle_batch(self, lines, *, source_lang: str, target_lang: str):
            if lines[0] == "first":
                time.sleep(0.02)
            completed.append(lines[0])
            return [f"translated-{lines[0]}"]

    config = load_config()
    config.translation.subtitle_batch_size = 1
    config.translation.subtitle_concurrency = 2
    svc = SubtitleService(config, ParallelTranslator())
    cues = [SubtitleCue(0.0, 1.0, "first"), SubtitleCue(1.0, 2.0, "second")]

    translated = svc.translate_segmented_cues(cues, source_lang="en", target_lang="zh-CN")

    assert completed == ["second", "first"]
    assert [cue.translation for cue in translated] == ["translated-first", "translated-second"]
