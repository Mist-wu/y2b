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

默认模型：

```yaml
ai:
  model: deepseek-v4-flash
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

字幕样式可以在 `subtitle_style` 中调整。默认效果是底部双语字幕：中文大字号粗黑描边，英文小字号黑描边。
