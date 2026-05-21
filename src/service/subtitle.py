from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


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

    def translate_cues(
        self,
        cues: list[SubtitleCue],
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[SubtitleCue]:
        cues = self._segment_cues_with_deepseek(cues, source_lang=source_lang)
        batch_size = max(1, int(self.config.translation.subtitle_batch_size))
        translated_total = 0
        for i in range(0, len(cues), batch_size):
            batch = cues[i : i + batch_size]
            lines = [cue.text for cue in batch]
            if self.logger:
                self.logger.info(f"翻译字幕批次 {i // batch_size + 1}: {len(lines)} 条")
            translations = self._translate_lines_resilient(
                lines,
                source_lang=source_lang,
                target_lang=target_lang,
            )
            for cue, text in zip(batch, translations, strict=True):
                cue.translation = text
                translated_total += 1
        if self.logger:
            self.logger.info(f"字幕翻译完成，共 {translated_total} 条")
        return cues

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
        cn_margin = max(40, round(height * style.cn_margin_ratio))
        cn_single_line_margin = max(40, round(height * style.cn_single_line_margin_ratio))
        en_margin = max(24, round(height * style.en_margin_ratio))
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
Style: CN,{style.font_cn},{cn_size},&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,{cn_outline},0,2,60,60,{cn_margin},1
Style: CN1,{style.font_cn},{cn_size},&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,{cn_outline},0,2,60,60,{cn_single_line_margin},1
Style: EN,{style.font_en},{en_size},&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,{en_outline},0,2,60,60,{en_margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = [header]
        for cue in cues:
            if cue.end <= cue.start:
                continue
            start = self._ass_time(cue.start)
            end = self._ass_time(cue.end)
            cn_wrapped = self._wrap_text(cue.translation or cue.text, max_chars=max(14, round(width / 54)))
            cn_style = "CN" if "\n" in cn_wrapped else "CN1"
            cn = self._ass_escape(cn_wrapped)
            en = self._ass_escape(self._wrap_text(cue.text, max_chars=max(36, round(width / 32))))
            lines.append(f"Dialogue: 1,{start},{end},{cn_style},,0,0,0,,{cn}\n")
            lines.append(f"Dialogue: 0,{start},{end},EN,,0,0,0,,{en}\n")

        output.write_text("".join(lines), encoding="utf-8")
        return output

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

    def _post_process_cues(self, cues: list[SubtitleCue]) -> list[SubtitleCue]:
        cues = self._merge_sentence_fragments(cues)
        cues = self._close_short_gaps(cues)
        return cues

    def _segment_cues_with_deepseek(self, cues: list[SubtitleCue], *, source_lang: str) -> list[SubtitleCue]:
        if not cues:
            return []
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
        if self.logger:
            self.logger.info(f"DeepSeek 智能分句完成: {len(cues)} -> {len(segmented)} 条")
        return segmented

    def _segment_one_batch(self, batch_index: int, batch: list[SubtitleCue], *, source_lang: str) -> list[SubtitleCue]:
        if self.logger:
            self.logger.info(f"DeepSeek v4 flash 非思考智能分句批次 {batch_index + 1}: {len(batch)} 个字幕 token")
        try:
            ranges = self.translator.segment_subtitle_batch(
                [cue.text for cue in batch],
                source_lang=source_lang,
            )
            return self._apply_ai_ranges(batch, ranges)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"DeepSeek 智能分句失败，使用规则分句回退: {e}")
            return self._merge_sentence_fragments(batch)

    def _apply_ai_ranges(self, cues: list[SubtitleCue], ranges: list[dict[str, int]]) -> list[SubtitleCue]:
        if not ranges:
            raise RuntimeError("DeepSeek 分句返回空结果")
        result: list[SubtitleCue] = []
        expected_start = 0
        last_index = len(cues) - 1
        for item in ranges:
            start = int(item["start"])
            end = int(item["end"])
            if start != expected_start or end < start or end > last_index:
                raise RuntimeError(f"DeepSeek 分句索引不连续: expected_start={expected_start}, item={item}")
            group = cues[start : end + 1]
            text = self._dedupe_repeated_words(" ".join(cue.text for cue in group).strip())
            duration = group[-1].end - group[0].start
            if len(text.split()) > 24 or duration > 8.0:
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
            raise RuntimeError(f"DeepSeek 分句未覆盖全部 token: covered={expected_start}, total={len(cues)}")
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
                ):
                    nxt = cues[i + 1]
                    merged_text = self._dedupe_repeated_words(f"{current.text} {nxt.text}".strip())
                    merged_duration = nxt.end - current.start
                    if len(merged_text.split()) <= 36 and merged_duration <= 10.0:
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
        max_duration = 5.2
        max_chars = 100
        max_words = 17

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
                and combined_duration <= (max_duration + 1.4 if relaxed_limit else max_duration)
                and len(combined_text) <= (max_chars + 40 if relaxed_limit else max_chars)
                and len(combined_text.split()) <= (max_words + 6 if relaxed_limit else max_words)
            )
            if should_merge:
                current.end = max(current.end, cue.end)
                current.text = self._dedupe_repeated_words(combined_text)
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

    def _dedupe_repeated_words(self, text: str) -> str:
        words = text.split()
        if len(words) < 6:
            return text
        lower = [w.lower() for w in words]
        max_n = min(len(words) // 2, 12)
        for n in range(max_n, 2, -1):
            if lower[:n] == lower[n : 2 * n]:
                return " ".join([*words[:n], *words[2 * n :]]).strip()
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

    def _wrap_text(self, text: str, *, max_chars: int) -> str:
        text = (text or "").strip()
        if len(text) <= max_chars:
            return text
        chunks: list[str] = []
        current = ""
        for token in re.split(r"(\s+)", text):
            if not token:
                continue
            if len(current) + len(token) > max_chars and current:
                chunks.append(current.strip())
                current = token.strip()
            else:
                current += token
        if current.strip():
            chunks.append(current.strip())
        if len(chunks) <= 1:
            chunks = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
        return "\n".join(chunks[:2])
