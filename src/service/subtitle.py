from __future__ import annotations

import html
import re
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
        batch_size = max(1, int(self.config.translation.subtitle_batch_size))
        translated_total = 0
        for i in range(0, len(cues), batch_size):
            batch = cues[i : i + batch_size]
            lines = [cue.text for cue in batch]
            if self.logger:
                self.logger.info(f"翻译字幕批次 {i // batch_size + 1}: {len(lines)} 条")
            translations = self.translator.translate_subtitle_batch(
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
            cn = self._ass_escape(self._wrap_text(cue.translation or cue.text, max_chars=max(14, round(width / 54))))
            en = self._ass_escape(self._wrap_text(cue.text, max_chars=max(36, round(width / 32))))
            lines.append(f"Dialogue: 1,{start},{end},CN,,0,0,0,,{cn}\n")
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
            body: list[str] = []
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            clean = self._clean_text(" ".join(body))
            if clean:
                self._append_cue(cues, SubtitleCue(start=start, end=end, text=clean))
            i += 1
        return cues

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
            if clean:
                self._append_cue(cues, SubtitleCue(start=start, end=end, text=clean))
        return cues

    def _append_cue(self, cues: list[SubtitleCue], cue: SubtitleCue) -> None:
        # YouTube auto captions may contain exact duplicate overlapping cues.
        if cues and cues[-1].text == cue.text and abs(cues[-1].start - cue.start) < 1.0:
            cues[-1].end = max(cues[-1].end, cue.end)
            return
        cues.append(cue)

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
