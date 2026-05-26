# AGENTS.md

## 范围

- `y2b` 是单视频 Python CLI：YouTube 字幕/视频 -> DeepSeek 翻译 -> 双语 ASS -> ffmpeg 压制 -> 可选上传 Bilibili。
- 只处理用户显式传入的视频链接；不要恢复频道监控、scheduler 或 polling。

## 默认行为

- 语言：`en` -> `zh-CN`；模型：`deepseek-v4-flash`，`ai.reasoning=false`。
- `.env` 中提供 `DEEPSEEK_API_KEY`；默认从项目根目录读取，可由 `Y2B_HOME` 改变运行根目录。
- YouTube 默认读取 Chrome cookies；容器中改用挂载的 cookies 文件。
- 字幕为底部双语：中文在上、英文在下；字体优先使用 `fonts/`。
- 默认压制 profile 为 `quality`；macOS 快速压制可使用 `--render-profile fast`。

## 命令

```bash
uv sync
uv run y2b check
uv run y2b login youtube --browser chrome
uv run y2b login bilibili
uv run y2b translate "<youtube-url>" --no-upload --keep-files
uv run y2b translate "<youtube-url>" --resume-job <job_id> --no-upload --keep-files
uv run y2b jobs
uv run y2b logs -f
```

## 代码边界

- 配置：`src/config/config.py`、`src/config/config.yaml`。
- AI 抽象：`src/infra/ai_client.py`；新增供应商实现 `BaseLLMClient`，不要写进业务服务。
- 字幕：`src/service/subtitle.py`；下载：`src/infra/yt_dlp.py`、`src/service/downloader.py`。
- 压制：`src/infra/ffmpeg.py`、`src/service/renderer.py`；流程：`src/service/pipeline.py`；入口：`src/cli.py`。
- 不提交 `.env`、cookies、视频、字幕产物、日志、state db 或其他运行产物。

## 字幕与恢复约束

- 保留自动字幕细粒度 token；AI 分句 range 必须连续、无重叠且无遗漏。
- 避免在介词、连词、冠词和助动词处断句；短 gap 应闭合。
- AI 分句失败必须回退到本地规则，字幕拆分不得丢字。
- 恢复任务可复用阶段缓存；成片仅在 ASS、输入视频与编码 profile 清单一致时复用。

## 修改与验证

- 配置字段变更同步更新 YAML、README 与测试。
- 字幕、流程、压制行为变更必须补充 `tests/`。
- 提交前运行：

```bash
uv run python -m compileall src
uv run pytest -q
uv run y2b check
```

- 字幕改动可用下列流程手测并抽帧检查：

```bash
uv run y2b translate "https://www.youtube.com/watch?v=b9RgHa1CnH4" --no-upload --keep-files
ffmpeg -y -ss 00:04:20 -i output/b9RgHa1CnH4.bilingual.mp4 -frames:v 1 -update 1 output/preview.jpg
```
