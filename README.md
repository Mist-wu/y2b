# y2b

YouTube 单视频字幕翻译、硬字幕压制并上传到 Bilibili 的 Python CLI 工具。

## 功能

- 配置 YouTube cookies
- 调用 `biliup` 登录 Bilibili
- 检查 `yt-dlp` / `ffmpeg` / `biliup` / DeepSeek API
- 传入单个 YouTube 视频链接自动处理：
  - 下载视频
  - 下载英文字幕
  - 使用 DeepSeek v4 flash 翻译为中文字幕
  - 生成底部双语 ASS 字幕（中文大字在上，英文小字在下）
  - 使用 ffmpeg 硬字幕压制
  - 上传到 Bilibili
- 使用 SQLite 记录任务，可查看进度和日志

> 原频道监控能力已移除。本项目现在只处理用户显式传入的单个 YouTube 视频链接。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

或使用 uv：

```bash
uv sync
```

系统需要安装：

- ffmpeg / ffprobe
- 可访问 YouTube、Bilibili、DeepSeek API 的网络环境

## 配置 DeepSeek

在项目根目录创建 `.env`：

```env
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

默认使用 DeepSeek v4 flash 非思考模式：

```yaml
ai:
  provider: deepseek
  model: deepseek-v4-flash
  reasoning: false
```

## 登录

### YouTube

交互式：

```bash
y2b login youtube
```

导入 cookies 文件：

```bash
y2b login youtube --cookies-file ./www.youtube.com_cookies.txt
```

使用浏览器 cookies：

```bash
y2b login youtube --browser chrome
```

### Bilibili

```bash
y2b login bilibili
```

## 检查环境

```bash
y2b check
```

可对指定视频做 YouTube 访问探针：

```bash
y2b check --probe-url "https://www.youtube.com/watch?v=xxxx"
```

## 处理单个视频

```bash
y2b translate "https://www.youtube.com/watch?v=xxxx"
```

常用参数：

```bash
y2b translate <url> \
  --source-lang en \
  --target-lang zh-CN \
  --tag 荒野乱斗 \
  --tag 游戏 \
  --tid 4
```

未显式传 `--tag` / `--tid` 时，上传前会让 DeepSeek 自动推荐 1-4 个标签，并从白名单分区中选择一个分区；当前白名单为 `知识(36)`、`游戏(4)`。命令行参数始终优先。

`bilibili.upload.line` 默认保持为 `null`，由 `biliup` 使用默认上传线路；如需手动指定，必须使用当前 `biliup upload --help` 列出的合法线路值。

Bilibili 创作声明可通过 `bilibili.upload` 配置：`copyright: 1` 表示自制，`2` 表示转载；`no_reprint: 0` 表示不勾选“未经作者允许，禁止转载”，`1` 表示勾选。

只生成压制后视频，不上传：

```bash
y2b translate <url> --no-upload --keep-files
```

`repost` 是 `translate` 的别名：

```bash
y2b repost <url>
```

## 查看任务和日志

```bash
y2b jobs
y2b status <job_id>
y2b logs -f
```

## 配置文件

主配置：

```text
src/config/config.yaml
```

字幕样式可以在 `subtitle_style` 中调整。默认效果是底部双语字幕：中文更醒目、描边更清晰，英文更小用于对照；整体偏教学/游戏解说可读性，不追求纪录片重字幕风格。

项目内置 `fonts/` 字体目录，默认使用：

- 中文：Source Han Sans CN Medium
- 英文：Inter SemiBold

ffmpeg 压制时会通过 `ass` filter 的 `fontsdir` 加载该目录，减少跨平台字体缺失问题。

## Docker

```bash
docker build -t y2b .
docker run --rm -it \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/downloads:/app/downloads" \
  -v "$PWD/output:/app/output" \
  -v "$PWD/logs:/app/logs" \
  y2b check
```

处理视频示例：

```bash
docker run --rm -it --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/downloads:/app/downloads" \
  -v "$PWD/output:/app/output" \
  -v "$PWD/logs:/app/logs" \
  y2b translate "https://www.youtube.com/watch?v=xxxx" --no-upload --keep-files
```
