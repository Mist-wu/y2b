from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


_FILLER_WORDS = {"um", "uh", "er", "erm", "hmm", "mm", "mmm", "yeah", "yep", "yup", "oh", "ah"}
_EDGE_FILLER_WORDS = {"um", "uh", "er", "erm", "hmm", "mm", "mmm", "yeah", "yep", "yup"}


@dataclass
class SubtitleCue:
    start: float
    end: float
    text: str
    translation: str | None = None


class SubtitleService:
    def __init__(self, config, translator, logger=None):
        self.config = config
        self.translator = translator
        self.logger = logger

    def parse(self, path: str | Path) -> list[SubtitleCue]:
        path = Path(path)
        if path.suffix.lower() == ".srt":
            return self._parse_srt(path)
        return self._parse_vtt(path)

    def save_cues(self, cues: list[SubtitleCue], path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps([cue.__dict__ for cue in cues], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output

    def load_cues(self, path: str | Path) -> list[SubtitleCue]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise RuntimeError("字幕缓存格式无效")
        cues = [SubtitleCue(**item) for item in raw if isinstance(item, dict)]
        if len(cues) != len(raw) or not cues:
            raise RuntimeError("字幕缓存为空或包含无效条目")
        return cues

    def segment_cues(self, cues: list[SubtitleCue], *, source_lang: str) -> list[SubtitleCue]:
        return self._segment_cues_with_deepseek(cues, source_lang=source_lang)

    def translate_segmented_cues(
        self,
        cues: list[SubtitleCue],
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[SubtitleCue]:
        batch_size = max(1, int(self.config.translation.subtitle_batch_size))
        concurrency = int(self.config.translation.subtitle_concurrency)
        batches = [cues[i : i + batch_size] for i in range(0, len(cues), batch_size)]
        translated_total = 0
        if concurrency <= 1 or len(batches) <= 1:
            translated_batches = [
                self._translate_one_batch(i, batch, source_lang=source_lang, target_lang=target_lang)
                for i, batch in enumerate(batches)
            ]
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as pool:
                futures = [
                    pool.submit(self._translate_one_batch, i, batch, source_lang=source_lang, target_lang=target_lang)
                    for i, batch in enumerate(batches)
                ]
                translated_batches = [future.result() for future in futures]
        for batch, translations in zip(batches, translated_batches, strict=True):
            for cue, text in zip(batch, translations, strict=True):
                cue.translation = text
                translated_total += 1
        if self.logger:
            self.logger.info(f"字幕翻译完成，共 {translated_total} 条")
        return cues

    def _translate_one_batch(
        self,
        batch_index: int,
        batch: list[SubtitleCue],
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        lines = [cue.text for cue in batch]
        if self.logger:
            self.logger.info(f"翻译字幕批次 {batch_index + 1}: {len(lines)} 条")
        return self._translate_lines_resilient(
            lines,
            source_lang=source_lang,
            target_lang=target_lang,
        )

    def write_bilingual_ass(
        self,
        cues: list[SubtitleCue],
        output_path: str | Path,
        *,
        width: int,
        height: int,
    ) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        style = self.config.subtitle_style
        cn_size = max(24, round(height * style.cn_font_ratio))
        en_size = max(16, round(height * style.en_font_ratio))
        en_margin = max(24, round(height * style.en_margin_ratio))
        cn_default_margin = self._bilingual_cn_margin(
            height=height,
            en_margin=en_margin,
            en_size=en_size,
            en_line_count=1,
        )
        cn_outline = max(2, round(height * style.cn_outline_ratio))
        en_outline = max(2, round(height * style.en_outline_ratio))

        header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CN,{style.font_cn},{cn_size},&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,{cn_outline},0,2,60,60,{cn_default_margin},1
Style: EN,{style.font_en},{en_size},&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,{en_outline},0,2,60,60,{en_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = [header]
        cn_max_width = self._subtitle_max_display_width(width, cn_size, language="cjk")
        en_max_width = self._subtitle_max_display_width(width, en_size, language="latin")
        for cue in cues:
            if cue.end <= cue.start:
                continue
            for display_cue in self._split_cue_for_single_line_cn(cue, max_chars=cn_max_width):
                start = self._ass_time(display_cue.start)
                end = self._ass_time(display_cue.end)
                cn_text = re.sub(r"\s+", " ", (display_cue.translation or display_cue.text).strip())
                if not cn_text:
                    continue
                en_wrapped = self._wrap_text(
                    display_cue.text,
                    max_chars=en_max_width,
                    max_lines=3,
                    label="英文字幕",
                )
                cn_margin = self._bilingual_cn_margin(
                    height=height,
                    en_margin=en_margin,
                    en_size=en_size,
                    en_line_count=self._subtitle_line_count(en_wrapped),
                )
                cn = r"{\q2}" + self._ass_escape(cn_text)
                en = self._ass_escape(en_wrapped)
                lines.append(f"Dialogue: 1,{start},{end},CN,,0,0,{cn_margin},,{cn}\n")
                lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,{en_margin},,{en}\n")

        output.write_text("".join(lines), encoding="utf-8")
        return output

    def _split_cue_for_single_line_cn(self, cue: SubtitleCue, *, max_chars: int) -> list[SubtitleCue]:
        cn_text = re.sub(r"\s+", " ", (cue.translation or cue.text or "").strip())
        if not cn_text or self._display_width(cn_text) <= max_chars:
            return [cue]

        cn_parts = self._split_by_display_width(cn_text, max_chars)
        if len(cn_parts) <= 1:
            return [cue]

        min_part_duration = 0.35
        duration = cue.end - cue.start
        if duration < len(cn_parts) * min_part_duration:
            if self.logger:
                self.logger.warning("中文字幕过长但显示时间不足，保留完整单条字幕以避免内容丢失。")
            return [cue]

        en_parts = self._split_text_for_parallel_cues(cue.text, len(cn_parts))
        weights = [max(1, self._display_width(part)) for part in cn_parts]
        total_weight = sum(weights)
        result: list[SubtitleCue] = []
        current_start = cue.start
        for index, (cn_part, weight) in enumerate(zip(cn_parts, weights, strict=True)):
            if index == len(cn_parts) - 1:
                current_end = cue.end
            else:
                current_end = current_start + duration * weight / total_weight
                current_end = min(cue.end, max(current_start + min_part_duration, current_end))
            result.append(
                SubtitleCue(
                    start=current_start,
                    end=current_end,
                    text=en_parts[index] if index < len(en_parts) else cue.text,
                    translation=cn_part,
                )
            )
            current_start = current_end
        return [item for item in result if item.end > item.start]

    def _split_text_for_parallel_cues(self, text: str, parts: int) -> list[str]:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if parts <= 1 or not text:
            return [text]
        words = text.split()
        if len(words) >= parts * 2:
            return self._split_words_evenly(words, parts)
        text_parts = self._split_text_by_display_width(text, parts)
        if len(text_parts) == parts:
            return text_parts
        if len(text_parts) > parts:
            return [*text_parts[: parts - 1], " ".join(text_parts[parts - 1 :]).strip()]
        return [*text_parts, *[""] * (parts - len(text_parts))]

    def _parse_vtt(self, path: Path) -> list[SubtitleCue]:
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        cues: list[SubtitleCue] = []
        i = 0
        while i < len(lines):
            line = lines[i].strip("\ufeff ")
            if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
                i += 1
                continue
            if "-->" not in line and i + 1 < len(lines) and "-->" in lines[i + 1]:
                i += 1
                line = lines[i].strip()
            if "-->" not in line:
                i += 1
                continue
            start, end = self._parse_time_range(line)
            i += 1
            # YouTube VTT may put a whitespace-only line immediately after the timing line.
            # Treat leading blank lines as cue padding, not as cue terminators.
            while i < len(lines) and not lines[i].strip():
                i += 1
            body: list[str] = []
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            raw_body = self._pick_vtt_body_text(body)
            if re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", raw_body):
                for timed_cue in self._split_timed_vtt_cue(start, end, raw_body):
                    self._append_cue(cues, timed_cue)
            else:
                clean = self._clean_text(raw_body)
                if clean and (end - start) >= 0.2:
                    self._append_cue(cues, SubtitleCue(start=start, end=end, text=clean))
            i += 1
        return cues

    def _segment_cues_with_deepseek(self, cues: list[SubtitleCue], *, source_lang: str) -> list[SubtitleCue]:
        if not cues:
            return []
        cues = self._trim_unusually_long_cues(cues)
        batch_size = int(self.config.translation.segmentation_batch_size)
        concurrency = int(self.config.translation.segmentation_concurrency)
        batches = [(offset, cues[offset : offset + batch_size]) for offset in range(0, len(cues), batch_size)]
        if concurrency <= 1 or len(batches) <= 1:
            segmented_batches = [self._segment_one_batch(idx, batch, source_lang=source_lang) for idx, (_, batch) in enumerate(batches)]
        else:
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches))) as pool:
                futures = [
                    pool.submit(self._segment_one_batch, idx, batch, source_lang=source_lang)
                    for idx, (_, batch) in enumerate(batches)
                ]
                segmented_batches = [future.result() for future in futures]
        segmented: list[SubtitleCue] = []
        for grouped in segmented_batches:
            segmented.extend(grouped)
        segmented = self._repair_continuation_boundaries(segmented)
        segmented = self._close_short_gaps(segmented)
        segmented = self._trim_unusually_long_cues(segmented)
        segmented = self._merge_orphan_short_cues(segmented)
        segmented = self._clean_filler_cues(segmented)
        segmented = self._close_short_gaps(segmented)
        if self.logger:
            self.logger.info(f"DeepSeek 智能分句完成: {len(cues)} -> {len(segmented)} 条")
        return segmented

    def _segment_one_batch(self, batch_index: int, batch: list[SubtitleCue], *, source_lang: str) -> list[SubtitleCue]:
        if self.logger:
            self.logger.info(f"分句批次 {batch_index + 1}: {len(batch)} 个字幕 token")
        try:
            ranges = self.translator.segment_subtitle_batch(
                [cue.text for cue in batch],
                source_lang=source_lang,
            )
            return self._apply_ai_ranges(batch, ranges)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"智能分句失败，使用规则分句回退: {e}")
            return self._merge_sentence_fragments(batch)

    def _apply_ai_ranges(self, cues: list[SubtitleCue], ranges: list[dict[str, int]]) -> list[SubtitleCue]:
        if not ranges:
            raise RuntimeError("分句返回空结果")
        result: list[SubtitleCue] = []
        expected_start = 0
        last_index = len(cues) - 1
        for item in ranges:
            start = int(item["start"])
            end = int(item["end"])
            if start != expected_start or end < start or end > last_index:
                raise RuntimeError(f"分句索引不连续: expected_start={expected_start}, item={item}")
            group = cues[start : end + 1]
            text = self._clean_caption_text(" ".join(cue.text for cue in group).strip())
            duration = group[-1].end - group[0].start
            if len(text.split()) > 20 or duration > 7.0:
                result.extend(self._merge_sentence_fragments(group))
            else:
                result.append(
                    SubtitleCue(
                        start=group[0].start,
                        end=group[-1].end,
                        text=text,
                    )
                )
            expected_start = end + 1
        if expected_start != len(cues):
            raise RuntimeError(f"分句未覆盖全部 token: covered={expected_start}, total={len(cues)}")
        return result

    def _repair_continuation_boundaries(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        if len(cues) < 2:
            return cues
        changed = True
        while changed:
            changed = False
            repaired: list[SubtitleCue] = []
            i = 0
            while i < len(cues):
                current = cues[i]
                if i + 1 < len(cues) and (
                    self._ends_with_continuation_word(current.text)
                    or self._starts_with_continuation_word(cues[i + 1].text)
                    or self._has_soft_continuation_boundary(current.text, cues[i + 1].text)
                ):
                    nxt = cues[i + 1]
                    merged_text = self._clean_caption_text(f"{current.text} {nxt.text}".strip())
                    merged_duration = nxt.end - current.start
                    if len(merged_text.split()) <= 24 and merged_duration <= 9.0:
                        merged = SubtitleCue(
                            start=current.start,
                            end=nxt.end,
                            text=merged_text,
                        )
                        repaired.append(merged)
                        i += 2
                        changed = True
                        continue
                repaired.append(current)
                i += 1
            cues = repaired
        return cues

    def _merge_sentence_fragments(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        """Merge word/phrase-level auto captions into sentence-like subtitle units.

        YouTube auto captions are often split every 1-2 seconds, which causes Chinese
        translations to become fragments like "方法，...".  We merge adjacent cues
        before translation so each translated subtitle changes closer to sentence boundaries.
        """
        if not cues:
            return []

        max_gap = 0.45
        max_duration = 4.8
        max_chars = 80
        max_words = 15

        merged: list[SubtitleCue] = []
        current = SubtitleCue(start=cues[0].start, end=cues[0].end, text=cues[0].text)

        for cue in cues[1:]:
            gap = cue.start - current.end
            combined_text = f"{current.text} {cue.text}".strip()
            combined_duration = cue.end - current.start
            current_bad_end = self._ends_with_continuation_word(current.text)
            next_continuation = self._starts_with_continuation_word(cue.text)
            relaxed_limit = current_bad_end or next_continuation
            current_words = len(current.text.split())
            next_starts_new_sentence = self._starts_new_sentence_word(cue.text)
            should_merge = (
                gap <= max_gap
                and not self._looks_sentence_complete(current.text)
                and not (
                    current_words >= 5
                    and not current_bad_end
                    and next_starts_new_sentence
                    and not next_continuation
                    and (current.end - current.start) >= 1.2
                )
                and combined_duration <= (max_duration + 1.0 if relaxed_limit else max_duration)
                and len(combined_text) <= (max_chars + 24 if relaxed_limit else max_chars)
                and len(combined_text.split()) <= (max_words + 3 if relaxed_limit else max_words)
            )
            if should_merge:
                current.end = max(current.end, cue.end)
                current.text = self._clean_caption_text(combined_text)
                continue
            merged.append(current)
            current = SubtitleCue(start=cue.start, end=cue.end, text=cue.text)

        merged.append(current)
        if self.logger and len(merged) != len(cues):
            self.logger.info(f"字幕按句合并: {len(cues)} -> {len(merged)} 条")
        return merged

    def _close_short_gaps(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        """Remove tiny blank flashes between adjacent subtitles.

        If two subtitles are separated by only a very short gap, extend the previous
        subtitle to the next subtitle's start time so the screen transitions directly.
        """
        if len(cues) < 2:
            return cues
        threshold = 0.30
        for prev, nxt in zip(cues, cues[1:]):
            gap = nxt.start - prev.end
            if 0 <= gap <= threshold:
                prev.end = nxt.start
            elif -threshold <= gap < 0:
                prev.end = nxt.start
        return cues

    def _merge_orphan_short_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        """Attach tiny trailing fragments to adjacent subtitles.

        AI/rule segmentation may leave the last noun of a phrase as its own subtitle,
        e.g. "games incredible" + "story".  If a very short cue touches the
        previous cue and the combined line is still modest, merge it back.
        """
        if len(cues) < 2:
            return cues

        merged: list[SubtitleCue] = []
        changed = 0
        for cue in cues:
            if not merged:
                merged.append(cue)
                continue

            prev = merged[-1]
            gap = cue.start - prev.end
            cue_words = re.findall(r"[A-Za-z0-9']+", cue.text)
            combined_text = self._clean_caption_text(f"{prev.text} {cue.text}".strip())
            combined_duration = cue.end - prev.start
            should_attach_to_prev = (
                0 <= gap <= 0.35
                and len(cue_words) <= 2
                and not self._looks_sentence_complete(prev.text)
                and len(combined_text.split()) <= 18
                and combined_duration <= 7.0
            )
            if should_attach_to_prev:
                prev.end = cue.end
                prev.text = combined_text
                changed += 1
                continue
            merged.append(cue)

        if self.logger and changed:
            self.logger.info(f"已合并孤立短字幕: {changed} 条")
        return merged

    def _trim_unusually_long_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        """Shorten or split captions that are too long for comfortable reading.

        YouTube auto captions sometimes leave the last word/short phrase visible until
        the end of the video.  Keep normal long sentences intact, but cap cues whose
        duration is far longer than their text can reasonably occupy.  After AI/rule
        segmentation, also split very dense long cues on word boundaries so a single
        subtitle does not stay on screen for 8-10 seconds.
        """
        if not cues:
            return []

        trimmed: list[SubtitleCue] = []
        trim_changed = 0
        split_changed = 0
        for idx, cue in enumerate(cues):
            duration = cue.end - cue.start
            if duration <= 0:
                trimmed.append(cue)
                continue

            candidate = cue
            reasonable_duration = self._reasonable_cue_duration(cue.text)
            trigger_duration = max(6.0, reasonable_duration * 1.8, reasonable_duration + 1.5)
            if duration > trigger_duration:
                new_end = cue.start + reasonable_duration
                if idx + 1 < len(cues) and cues[idx + 1].start > cue.start:
                    new_end = min(new_end, cues[idx + 1].start)
                new_end = max(cue.start + 0.35, min(new_end, cue.end))
                if new_end < cue.end - 0.25:
                    candidate = SubtitleCue(
                        start=cue.start,
                        end=new_end,
                        text=cue.text,
                        translation=cue.translation,
                    )
                    trim_changed += 1

            split = self._split_overlong_cue(candidate)
            if len(split) > 1:
                split_changed += 1
            trimmed.extend(split)

        if self.logger:
            if trim_changed:
                self.logger.info(f"已修剪异常超长字幕: {trim_changed} 条")
            if split_changed:
                self.logger.info(f"已拆分过长字幕: {split_changed} 条")
        return trimmed

    def _split_overlong_cue(self, cue: SubtitleCue) -> list[SubtitleCue]:
        duration = cue.end - cue.start
        text = cue.text.strip()
        words = text.split()
        compact_chars = len(re.sub(r"\s+", "", text))
        if duration <= 7.0 or (len(words) <= 20 and compact_chars <= 110):
            return [cue]

        parts = max(
            2,
            math.ceil(duration / 5.0),
            (len(words) + 17) // 18 if words else 1,
            (compact_chars + 89) // 90,
        )
        if words and len(words) >= parts * 3:
            text_parts = self._split_words_evenly(words, parts)
        else:
            text_parts = self._split_text_by_display_width(text, parts)
        if len(text_parts) <= 1:
            return [cue]

        part_count = len(text_parts)
        result: list[SubtitleCue] = []
        for i, part_text in enumerate(text_parts):
            start = cue.start + duration * i / part_count
            end = cue.start + duration * (i + 1) / part_count
            result.append(SubtitleCue(start=start, end=end, text=part_text, translation=cue.translation))
        return result

    def _split_words_evenly(self, words: list[str], parts: int) -> list[str]:
        boundaries: list[int] = []
        for i in range(1, parts):
            target = round(len(words) * i / parts)
            candidates = [
                idx
                for idx in range(max(1, target - 4), min(len(words), target + 5))
                if re.search(r"[,.!?;:。！？；：]$", words[idx - 1])
            ]
            boundary = min(candidates, key=lambda idx: abs(idx - target), default=target)
            boundary = max(boundaries[-1] + 1 if boundaries else 1, min(boundary, len(words) - 1))
            boundaries.append(boundary)

        result: list[str] = []
        start = 0
        for boundary in [*boundaries, len(words)]:
            part = " ".join(words[start:boundary]).strip()
            if part:
                result.append(part)
            start = boundary
        return result

    def _split_text_by_display_width(self, text: str, parts: int) -> list[str]:
        max_width = max(8, (self._display_width(text) + parts - 1) // parts)
        return self._split_by_display_width(text, max_width)

    def _reasonable_cue_duration(self, text: str) -> float:
        words = re.findall(r"[A-Za-z0-9']+", text)
        compact_chars = len(re.sub(r"\s+", "", text))
        if words:
            return max(1.2, len(words) * 0.55 + 1.0, compact_chars * 0.04 + 0.8)
        return max(1.2, compact_chars * 0.12 + 0.8)

    def _clean_caption_text(self, text: str) -> str:
        text = self._dedupe_repeated_words(text)
        text = self._strip_edge_fillers(text)
        text = self._normalize_asr_terms(text)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    def _normalize_asr_terms(self, text: str) -> str:
        replacements = (
            (r"\bread\s+CSV\b", "read_csv"),
            (r"\bdrop\s+n\s+a\b", "dropna"),
            (r"\btwo\s+period\b", "to_period"),
            (r"\bDot\s+Plot\b", "plot()"),
            (r"\by\s+Finance\b", "yfinance"),
            (r"\bIn-Place\b", "inplace"),
            (r"\bN\s+A\b", "NA"),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _dedupe_repeated_words(self, text: str) -> str:
        """Collapse adjacent duplicated word groups caused by rolling auto captions.

        Keep the cleanup conservative: programming/quant/game videos often repeat key
        terms on purpose, so we only remove immediately repeated phrases. Single-word
        repeats are only collapsed for obvious filler words such as "yeah yeah".
        """
        words = text.split()
        if len(words) < 2:
            return text

        result = words[:]
        changed = True
        while changed:
            changed = False
            keys = [self._dedupe_key(word) for word in result]
            i = 0
            while i < len(result):
                max_n = min((len(result) - i) // 2, 12)
                removed = False
                for n in range(max_n, 0, -1):
                    left = keys[i : i + n]
                    right = keys[i + n : i + 2 * n]
                    if left != right or not any(left):
                        continue
                    if n == 1 and left[0] not in _FILLER_WORDS:
                        continue
                    del result[i + n : i + 2 * n]
                    changed = True
                    removed = True
                    break
                if removed:
                    break
                i += 1
        return " ".join(result).strip()

    def _dedupe_key(self, word: str) -> str:
        return re.sub(r"^[^\w']+|[^\w']+$", "", word.lower())

    def _clean_filler_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        if not cues:
            return []

        cleaned: list[SubtitleCue] = []
        dropped = 0
        for cue in cues:
            if self._is_filler_only_cue(cue):
                if cleaned and 0 <= cue.start - cleaned[-1].end <= 0.6:
                    cleaned[-1].end = max(cleaned[-1].end, cue.end)
                dropped += 1
                continue

            text = self._clean_caption_text(cue.text)
            if not text:
                dropped += 1
                continue
            cleaned.append(SubtitleCue(cue.start, cue.end, text, cue.translation))

        if self.logger and dropped:
            self.logger.info(f"已清理无意义语气词字幕: {dropped} 条")
        return cleaned

    def _is_filler_only_cue(self, cue: SubtitleCue) -> bool:
        words = re.findall(r"[A-Za-z']+", cue.text.lower())
        if not words or len(words) > 3:
            return False
        duration = cue.end - cue.start
        return duration <= 1.5 and all(word in _FILLER_WORDS for word in words)

    def _strip_edge_fillers(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return text
        pattern = "|".join(sorted(map(re.escape, _EDGE_FILLER_WORDS), key=len, reverse=True))
        while True:
            stripped = re.sub(rf"^(?:{pattern})(?:[,.!?:;\-–—]+|\s+)+", "", text, flags=re.IGNORECASE).strip()
            if stripped == text or not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", stripped):
                break
            text = stripped
        while True:
            stripped = re.sub(rf"(?:\s+|[,.!?:;\-–—]+)(?:{pattern})$", "", text, flags=re.IGNORECASE).strip()
            if stripped == text or not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", stripped):
                break
            text = stripped
        return text

    def _split_timed_vtt_cue(self, start: float, end: float, raw_text: str) -> list[SubtitleCue]:
        """Split YouTube karaoke-style VTT cue into word-timed mini cues.

        This gives the sentence merger more legal cut points, so we can avoid
        awkward fragments such as "first five or n" separated from "elements".
        """
        raw_text = raw_text.strip()
        parts = re.split(r"(<\d{2}:\d{2}:\d{2}\.\d{3}>)", raw_text)
        result: list[SubtitleCue] = []
        current_start = start
        current_text = ""
        for part in parts:
            if not part:
                continue
            if re.fullmatch(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", part):
                ts = self._parse_time(part.strip("<>"))
                clean = self._clean_text(current_text)
                if clean:
                    result.append(SubtitleCue(start=current_start, end=max(current_start + 0.05, ts), text=clean))
                current_start = ts
                current_text = ""
            else:
                current_text += part
        clean = self._clean_text(current_text)
        if clean:
            result.append(SubtitleCue(start=current_start, end=end, text=clean))
        return [cue for cue in result if cue.text and cue.end > cue.start]

    def _ends_with_continuation_word(self, text: str) -> bool:
        words = re.findall(r"[A-Za-z0-9']+", text.lower())
        if not words:
            return False
        return words[-1] in {
            "a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "as", "by",
            "and", "or", "but", "if", "when", "while", "that", "which", "who", "from", "into",
            "we", "can", "could", "would", "should", "will", "our", "your", "their", "this",
            "these", "those", "is", "are", "was", "were", "be", "being", "been",
        }

    def _starts_new_sentence_word(self, text: str) -> bool:
        words = re.findall(r"[A-Za-z0-9']+", text.lower())
        if not words:
            return False
        return words[0] in {
            "over", "first", "let's", "there", "this", "what", "now", "turning",
            "annualized", "lastly", "if", "when", "all", "coming",
        }

    def _starts_with_continuation_word(self, text: str) -> bool:
        words = re.findall(r"[A-Za-z0-9']+", text.lower())
        if not words:
            return False
        return words[0] in {
            "of", "to", "in", "on", "at", "for", "with", "as", "by", "and", "or", "but",
            "that", "which", "who", "from", "into", "than", "then", "the", "a", "an", "up",
            "elements", "asset", "assets", "method", "methods", "function", "data", "frame",
        }

    def _has_soft_continuation_boundary(self, left: str, right: str) -> bool:
        """Catch ASR phrase splits without treating common words as global glue."""
        left_words = re.findall(r"[A-Za-z0-9']+", left.lower())
        right_words = re.findall(r"[A-Za-z0-9']+", right.lower())
        if not left_words or not right_words:
            return False
        left_tail = " ".join(left_words[-4:])
        right_head = right_words[0]
        return (
            (left_tail.endswith("adjusted close gives") and right_head == "us")
            or (left_tail.endswith("cash flows so") and right_head == "we")
            or (left_tail.endswith("this way returns") and right_head == "will")
        )

    def _looks_sentence_complete(self, text: str) -> bool:
        text = text.strip()
        if not text:
            return False
        if re.search(r"[.!?。！？…]['\")\]]*$", text):
            # Avoid treating common abbreviations as sentence endings.
            lower = text.lower()
            if re.search(r"\b(?:mr|mrs|ms|dr|prof|inc|ltd|vs|etc)\.$", lower):
                return False
            return True
        return False

    def _parse_srt(self, path: Path) -> list[SubtitleCue]:
        text = path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
        blocks = re.split(r"\n\s*\n", text)
        cues: list[SubtitleCue] = []
        for block in blocks:
            rows = [r.strip() for r in block.split("\n") if r.strip()]
            if not rows:
                continue
            timing_idx = next((idx for idx, row in enumerate(rows) if "-->" in row), -1)
            if timing_idx < 0:
                continue
            start, end = self._parse_time_range(rows[timing_idx])
            clean = self._clean_text(" ".join(rows[timing_idx + 1 :]))
            if clean and (end - start) >= 0.2:
                self._append_cue(cues, SubtitleCue(start=start, end=end, text=clean))
        return cues

    def _translate_lines_resilient(self, lines: list[str], *, source_lang: str, target_lang: str) -> list[str]:
        try:
            return self.translator.translate_subtitle_batch(
                lines,
                source_lang=source_lang,
                target_lang=target_lang,
            )
        except Exception as e:
            if len(lines) <= 1:
                if self.logger:
                    self.logger.warning(f"单条字幕翻译失败，使用原文回退: {e}")
                return lines
            mid = len(lines) // 2
            if self.logger:
                self.logger.warning(f"字幕批量翻译失败，拆分重试: {e}")
            return [
                *self._translate_lines_resilient(lines[:mid], source_lang=source_lang, target_lang=target_lang),
                *self._translate_lines_resilient(lines[mid:], source_lang=source_lang, target_lang=target_lang),
            ]

    def _append_cue(self, cues: list[SubtitleCue], cue: SubtitleCue) -> None:
        # YouTube auto captions may contain duplicate overlapping cues.
        if cues and cues[-1].text == cue.text and abs(cues[-1].start - cue.start) < 1.0:
            cues[-1].end = max(cues[-1].end, cue.end)
            return
        cues.append(cue)

    def _pick_vtt_body_text(self, body: list[str]) -> str:
        if not body:
            return ""
        # YouTube auto captions often repeat the previous line and put the new timed text on the last line.
        non_empty = [line.strip() for line in body if line.strip()]
        if not non_empty:
            return ""
        if any(re.search(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", line) for line in non_empty):
            return non_empty[-1]
        return " ".join(non_empty)

    def _parse_time_range(self, line: str) -> tuple[float, float]:
        left, right = line.split("-->", 1)
        right = right.strip().split()[0]
        return self._parse_time(left.strip()), self._parse_time(right.strip())

    def _parse_time(self, value: str) -> float:
        value = value.replace(",", ".")
        parts = value.split(":")
        if len(parts) == 3:
            h, m, s = parts
        elif len(parts) == 2:
            h = "0"
            m, s = parts
        else:
            return float(value)
        return int(h) * 3600 + int(m) * 60 + float(s)

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"<\d{2}:\d{2}:\d{2}\.\d{3}>", "", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _ass_time(self, seconds: float) -> str:
        centiseconds = int(round(max(0.0, seconds) * 100))
        cs = centiseconds % 100
        total_seconds = centiseconds // 100
        s = total_seconds % 60
        total_minutes = total_seconds // 60
        m = total_minutes % 60
        h = total_minutes // 60
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _ass_escape(self, text: str) -> str:
        return text.replace("{", "（").replace("}", "）").replace("\n", r"\N")

    def _wrap_text(
        self,
        text: str,
        *,
        max_chars: int,
        max_lines: int | None = 3,
        label: str | None = None,
    ) -> str:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text or self._display_width(text) <= max_chars:
            return text

        chunks: list[str] = []
        current = ""
        pending_space = False
        for token in re.split(r"(\s+)", text):
            if not token:
                continue
            if token.isspace():
                pending_space = bool(current)
                continue

            parts = [token]
            if self._display_width(token) > max_chars:
                parts = self._split_by_display_width(token, max_chars)

            for part in parts:
                if not part:
                    continue
                separator = " " if pending_space and current else ""
                candidate = f"{current}{separator}{part}" if current else part
                if current and self._display_width(candidate) > max_chars:
                    chunks.append(current.strip())
                    current = part.strip()
                else:
                    current = candidate
                pending_space = False

        if current.strip():
            chunks.append(current.strip())

        if max_lines is not None and len(chunks) > max_lines and self.logger:
            cue_label = label or "字幕"
            self.logger.warning(
                f"{cue_label}换行后为 {len(chunks)} 行，超过建议上限 {max_lines} 行；"
                "已保留完整内容，请考虑进一步拆分该字幕。"
            )
        return "\n".join(chunks)

    def _split_by_display_width(self, text: str, max_width: int) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        atoms = self._wrap_atoms(text)
        chunks: list[str] = []
        start = 0
        while start < len(atoms):
            while start < len(atoms) and atoms[start][1] == "space":
                start += 1
            if start >= len(atoms):
                break

            width = 0
            end = start
            while end < len(atoms) and width + self._display_width(atoms[end][0]) <= max_width:
                width += self._display_width(atoms[end][0])
                end += 1

            if end == len(atoms):
                chunk = "".join(atom for atom, _ in atoms[start:end]).strip()
                if chunk:
                    chunks.append(chunk)
                break

            if end == start:
                pieces = self._split_long_atom(atoms[start][0], max_width)
                chunks.extend(pieces[:-1])
                atoms[start] = (pieces[-1], atoms[start][1])
                if len(pieces) == 1:
                    start += 1
                continue

            boundary = self._choose_wrap_boundary(atoms, start, end, max_width)
            chunk = "".join(atom for atom, _ in atoms[start:boundary]).strip()
            if chunk:
                chunks.append(chunk)
            start = boundary

        return chunks

    def _wrap_atoms(self, text: str) -> list[tuple[str, str]]:
        atoms: list[tuple[str, str]] = []
        pattern = re.compile(r"[A-Za-z0-9_]+(?:[._'-][A-Za-z0-9_]+)*|\s+|.", re.DOTALL)
        for match in pattern.finditer(text):
            token = match.group(0)
            if token.isspace():
                atoms.append((" ", "space"))
            elif re.fullmatch(r"[A-Za-z0-9_]+(?:[._'-][A-Za-z0-9_]+)*", token):
                atoms.append((token, "word"))
            elif all(self._is_cjk(char) for char in token):
                atoms.append((token, "cjk"))
            else:
                atoms.append((token, "punct" if self._is_wrap_punctuation(token) else "other"))
        return atoms

    def _choose_wrap_boundary(
        self,
        atoms: list[tuple[str, str]],
        start: int,
        end: int,
        max_width: int,
    ) -> int:
        candidates: list[tuple[int, int, int]] = []
        width = 0
        for pos in range(start + 1, end + 1):
            width += self._display_width(atoms[pos - 1][0])
            priority = self._wrap_boundary_priority(atoms, pos)
            if priority > 0:
                candidates.append((priority, width, pos))
        if not candidates:
            return end

        preferred = [item for item in candidates if item[1] >= max_width * 0.45]
        pool = preferred or candidates
        priority = max(item[0] for item in pool)
        return max(item[2] for item in pool if item[0] == priority)

    def _wrap_boundary_priority(self, atoms: list[tuple[str, str]], pos: int) -> int:
        previous_text, previous_kind = atoms[pos - 1]
        next_kind = atoms[pos][1] if pos < len(atoms) else "end"
        if previous_kind == "space":
            return 5
        if self._is_wrap_punctuation(previous_text):
            return 6
        if next_kind == "punct" and atoms[pos][0] not in {'“', '‘', '（', '(', '《', '「'}:
            return 0
        if previous_kind in {"word", "cjk"} and next_kind in {"word", "cjk"} and previous_kind != next_kind:
            return 4
        if previous_kind == "cjk" and next_kind == "cjk":
            return 1
        return 0

    def _split_long_atom(self, text: str, max_width: int) -> list[str]:
        chunks: list[str] = []
        current = ""
        width = 0
        for char in text:
            char_width = self._display_width(char)
            if current and width + char_width > max_width:
                chunks.append(current)
                current = char
                width = char_width
            else:
                current += char
                width += char_width
        if current:
            chunks.append(current)
        return chunks

    def _subtitle_line_count(self, text: str) -> int:
        return max(1, len((text or "").splitlines()))

    def _bilingual_cn_margin(self, *, height: int, en_margin: int, en_size: int, en_line_count: int) -> int:
        style = self.config.subtitle_style
        en_lines = max(1, en_line_count)
        line_gap = max(4, round(height * 0.004))
        en_block_height = round(en_size * 1.05) * en_lines
        ratio_floor = (
            style.cn_single_line_margin_ratio
            if en_lines == 1
            else style.cn_single_line_wrapped_en_margin_ratio
        )
        return max(40, en_margin + en_block_height + line_gap, round(height * ratio_floor))

    def _subtitle_max_display_width(self, video_width: int, font_size: int, *, language: str) -> int:
        available_width = video_width * 0.94
        if language == "latin":
            return max(72, min(118, round(available_width / max(1, font_size * 0.55))))
        return max(48, min(76, round(available_width / max(1, font_size * 0.50))))

    def _is_wrap_punctuation(self, text: str) -> bool:
        return bool(text) and text[-1] in "，。！？、；：）」》】』』,.!?;:)]}"

    def _is_cjk(self, char: str) -> bool:
        return "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"

    def _display_width(self, text: str) -> int:
        width = 0
        for char in text:
            width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
        return width
