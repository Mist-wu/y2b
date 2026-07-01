# y2b

将单个 YouTube 视频翻译为中英双语硬字幕视频，并可上传到 Bilibili 的 Python CLI。

## 流程

1. 校验认证并获取视频信息。
2. 先下载并确认英文字幕，再下载视频。
3. 使用 DeepSeek v4 flash 非思考模式智能分句与翻译。
4. 生成中文在上、英文在下的双语 ASS，并用 ffmpeg 压制。
5. 可选生成投稿信息并通过 `biliup` 上传。

本项目不包含频道监控或定时抓取功能。

## 安装与配置

```bash
uv sync
```

系统需可用 `ffmpeg` / `ffprobe`，并可访问 YouTube、Bilibili 和 DeepSeek API。运行期间不会自动安装工具。

在项目根目录创建 `.env`：

```env
DEEPSEEK_API_KEY=你的_API_Key
```

默认配置位于 `src/config/config.yaml`：

- 翻译：`en` -> `zh-CN`，`deepseek-v4-flash`，`reasoning: false`
- 认证：YouTube 默认 `cookies_from_browser: chrome`
- 压制：默认 `quality`；`fast` 使用 macOS `h264_videotoolbox`
- 路径：相对路径基于项目根目录；可通过 `Y2B_HOME=/path/to/runtime` 指定数据目录

## 登录与检查

```bash
uv run y2b login youtube --browser chrome
uv run y2b login bilibili
uv run y2b check
uv run y2b check --probe-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

YouTube 也可导入 Netscape cookies 文件：

```bash
uv run y2b login youtube --cookies-file ./youtube_cookies.txt
```

## 使用

翻译、压制并上传：

```bash
uv run y2b translate "https://www.youtube.com/watch?v=VIDEO_ID"
```

只生成视频，不上传：

```bash
uv run y2b translate "<url>" --no-upload --keep-files
```

按阶段执行，便于只做特定动作：

```bash
uv run y2b translate "<url>" --stop-after subtitle --keep-files     # 只下载原始字幕
uv run y2b translate "<url>" --stop-after translation --keep-files  # 分句并翻译，输出翻译缓存 JSON
uv run y2b translate "<url>" --stop-after ass --keep-files          # 生成双语 ASS，不压制/不上传
uv run y2b translate "<url>" --stop-after render --keep-files       # 压制完成后停止
```

快速硬件压制：

```bash
uv run y2b translate "<url>" --no-upload --keep-files --render-profile fast
```

上传参数：

```bash
uv run y2b translate "<url>" --tag 翻译 --tag YouTube --tid 36
```

未传 `--tag` / `--tid` 时，程序会尝试 AI 推荐并回退到非空 `bilibili.default_tags`。`repost` 是 `translate` 的别名。

## 恢复任务

```bash
uv run y2b jobs
uv run y2b jobs --mark-interrupted
uv run y2b translate "<url>" --resume-job <job_id> --no-upload --keep-files
```

- `--no-upload` 不请求投稿标题或标签，等价于默认流程停在 `--stop-after render`。
- `--stop-after ass` 会用 YouTube metadata 中的分辨率生成 ASS，不下载视频。
- 分句与翻译阶段分别保存缓存；翻译批次支持 `translation.subtitle_concurrency` 并发。
- 恢复时可复用字幕、视频和翻译缓存；成片仅在 ASS、输入视频与编码 profile 清单一致时复用。

任务详情与日志：

```bash
uv run y2b status <job_id>
uv run y2b logs -f
```

## 输出

```text
downloads/<video_id>/<video_id>.bilingual.ass
output/<video_id>.bilingual.mp4
```

抽帧检查字幕：

```bash
ffmpeg -y -ss 00:04:20 -i output/<video_id>.bilingual.mp4 -frames:v 1 -update 1 output/preview.jpg
```

## Docker

容器不能读取宿主机 Chrome cookies。先将 YouTube cookies 放到 `data/youtube_cookies.txt`，再执行：

```bash
docker build -t y2b .
docker run --rm -it --env-file .env \
  -e Y2B_YOUTUBE__COOKIES_FROM_BROWSER=null \
  -e Y2B_YOUTUBE__COOKIES=/app/data/youtube_cookies.txt \
  -v "$PWD/data:/app/data" \
  -v "$PWD/downloads:/app/downloads" \
  -v "$PWD/output:/app/output" \
  -v "$PWD/logs:/app/logs" \
  y2b check
```

## 开发验证

```bash
uv run python -m compileall src
uv run pytest -q
uv run y2b check
```
