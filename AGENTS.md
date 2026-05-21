# AGENTS.md
## 项目定位

- 本项目是 Python CLI 工具 `y2b`，只处理用户显式传入的单个 YouTube 视频链接。
- 核心流程：YouTube 登录/下载视频与英文字幕 → DeepSeek v4 flash 非思考模式智能分句与翻译 → 生成双语 ASS 硬字幕 → ffmpeg 压制 → 可选上传 Bilibili。
- 原频道监控能力已废弃，不要恢复 scheduler/monitor/channel polling 相关逻辑。

## 重要默认行为

- 默认源语言：`en`；默认目标语言：`zh-CN`。
- 默认模型：`deepseek-v4-flash`，`ai.reasoning=false`。
- DeepSeek API Key 来自 `.env` 中的 `DEEPSEEK_API_KEY`。
- YouTube 认证优先使用 `cookies_from_browser: chrome`。
- 字幕样式：底部双语，中文在上且较大，英文在下且较小，白字黑描边。
- 字体优先使用项目内 `fonts/`，默认 `Source Han Sans CN` 与 `Inter`。

## 常用命令

```bash
uv sync
uv run y2b check
uv run y2b jobs
uv run y2b logs -f
uv run y2b translate "https://www.youtube.com/watch?v=VIDEO_ID" --no-upload --keep-files
uv run y2b repost "https://www.youtube.com/watch?v=VIDEO_ID"
```

登录命令：

```bash
uv run y2b login youtube --browser chrome
uv run y2b login bilibili
```

## 开发与修改规范

- 配置模型在 `src/config/config.py`，使用 Pydantic；默认配置在 `src/config/config.yaml`。
- LLM 抽象在 `src/infra/ai_client.py`；新增供应商时实现 `BaseLLMClient`，不要把供应商逻辑写进业务服务。
- 字幕解析、分句、ASS 生成在 `src/service/subtitle.py`。
- 下载逻辑在 `src/infra/yt_dlp.py` 与 `src/service/downloader.py`。
- ffmpeg 逻辑在 `src/infra/ffmpeg.py` 与 `src/service/renderer.py`。
- CLI 入口在 `src/cli.py`，命令应保持 argparse + Rich 风格。
- 单视频流程在 `src/service/pipeline.py`。
- 不要提交 cookies、`.env`、下载视频、输出视频、日志、state db 等运行产物。

## 字幕分句注意事项
- YouTube 自动字幕可能是词级/短片段级 VTT，解析后应保留细粒度 token 供智能分句使用。
- DeepSeek 分句应返回连续、不重叠、不遗漏的 token index range。
- 避免在介词、冠词、连词、助动词等位置断句，例如 `of/to/with/as/and/we/can`。
- 两条字幕间隔很短时应闭合 gap，避免画面闪烁。
- 如果 DeepSeek 分句失败，应回退到本地规则分句，不能让整条流程崩溃。

## 测试与验证

提交前至少运行：

```bash
uv run python -m compileall src
uv run pytest -q
uv run y2b check
```

字幕流程手动验证：

```bash
uv run y2b translate "https://www.youtube.com/watch?v=b9RgHa1CnH4" --no-upload --keep-files
```

重点检查生成文件：

```text
downloads/<video_id>/<video_id>.bilingual.ass
output/<video_id>.bilingual.mp4
```

可用 ffmpeg 抽帧检查字幕效果：

```bash
ffmpeg -y -ss 00:04:20 -i output/<video_id>.bilingual.mp4 -frames:v 1 output/preview.jpg
```

## Docker

```bash
docker build -t y2b .
docker run --rm -it --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/downloads:/app/downloads" \
  -v "$PWD/output:/app/output" \
  -v "$PWD/logs:/app/logs" \
  y2b check
```

## 代码质量要求

- 优先小步修改，保持现有 CLI 兼容。
- 修改配置字段时同步更新 `config.yaml`、README 和测试。
- 修改字幕解析/分句/ASS 输出时必须补充或更新 `tests/`。
- 错误信息应清晰可操作，用户命令输出尽量使用 Rich 表格或状态提示。
