# y2b Python CLI 工具重构规划

## 1. 目标

将当前项目从“频道监控自动搬运脚本”重构为一个面向单个 YouTube 视频链接的 Python CLI 工具。

新的核心使用流程：

```bash
y2b login youtube
y2b login bilibili
y2b check
y2b translate "https://www.youtube.com/watch?v=xxxx"
y2b jobs
y2b status <job_id>
y2b logs -f
y2b help
```

本次重构明确删除原有频道监控能力：

- 删除/废弃频道轮询逻辑
- 删除/废弃 `channels` 配置
- 删除/废弃“只处理启动后发布视频”的机制
- 删除/废弃 `monitor_backend`、`poll_interval`、`monitor_scan_limit` 等监控相关配置
- `main.py` 不再启动长期频道监控服务

新的程序只围绕用户显式传入的单个 YouTube 视频链接执行处理。

---

## 2. CLI 命令设计

### 2.1 登录 YouTube

```bash
y2b login youtube
```

交互式流程：

```text
请选择 YouTube cookie 输入方式：
1. 粘贴 Netscape cookies 内容
2. 指定 cookies.txt 文件路径
3. 使用浏览器 cookies，例如 chrome / edge / firefox
```

支持非交互参数：

```bash
y2b login youtube --cookies-file ./www.youtube.com_cookies.txt
y2b login youtube --browser chrome
```

默认保存位置：

```text
./data/youtube_cookies.txt
```

配置中记录：

```yaml
youtube:
  cookies: ./data/youtube_cookies.txt
  cookies_from_browser:
```

或：

```yaml
youtube:
  cookies:
  cookies_from_browser: chrome
```

---

### 2.2 登录 Bilibili

```bash
y2b login bilibili
```

内部调用：

```bash
biliup -u ./data/bilibili_cookies.json login
```

默认保存位置：

```text
./data/bilibili_cookies.json
```

---

### 2.3 检查状态

```bash
y2b check
```

检查内容：

```text
[依赖]
- Python 版本
- yt-dlp
- biliup
- ffmpeg

[认证]
- YouTube cookies 是否存在
- YouTube 是否可访问
- Bilibili cookies 是否存在

[AI 翻译]
- DeepSeek API Key 是否存在
- DeepSeek 模型配置是否存在

[目录]
- downloads/
- output/
- logs/
- data/state.db
```

示例输出：

```text
✅ yt-dlp 可用
✅ ffmpeg 可用
✅ YouTube 登录状态正常
✅ Bilibili cookies 已存在
✅ DeepSeek API Key 已配置

系统状态正常，可以执行：
y2b translate <YouTube视频链接>
```

---

### 2.4 单视频翻译搬运

```bash
y2b translate "https://www.youtube.com/watch?v=xxxx"
```

默认行为：

- 源语言：英语 `en`
- 目标语言：中文 `zh-CN`
- 翻译模型：DeepSeek v4 flash
- 默认下载 YouTube 视频
- 默认下载英文字幕
- 默认翻译为中文字幕
- 默认硬字幕压制到视频中
- 默认上传到 Bilibili

可选参数草案：

```bash
y2b translate <url> \
  --source-lang en \
  --target-lang zh-CN \
  --title "自定义标题" \
  --tag 荒野乱斗 \
  --tag 游戏 \
  --tid 4 \
  --no-upload \
  --keep-files
```

建议保留别名：

```bash
y2b repost <url>
```

`repost` 与 `translate` 执行相同流程。

---

### 2.5 任务查看

```bash
y2b jobs
```

示例输出：

```text
JOB_ID      VIDEO_ID      STATUS       STEP                  BVID
abc123      xxxx          uploading    上传到 Bilibili        -
def456      yyyy          uploaded     完成                  BV1xx...
```

查看单个任务：

```bash
y2b status <job_id>
```

实时日志：

```bash
y2b logs -f
```

---

## 3. 单视频处理流程

`y2b translate <url>` 的完整流水线：

```text
1. 创建任务记录
2. 检查运行环境
3. 检查 YouTube 登录状态
4. 检查 Bilibili 登录状态
5. 拉取 YouTube 视频元信息
6. 下载 YouTube 视频
7. 下载英文字幕
8. 英文字幕翻译为中文字幕
9. 生成 zh-CN.srt 或 zh-CN.ass
10. 使用 ffmpeg 将中文字幕硬字幕压制到视频
11. 使用 DeepSeek 翻译/生成 Bilibili 标题
12. 上传压制后视频到 Bilibili
13. 更新任务状态为 uploaded
```

状态流转：

```text
queued
checking
fetching_metadata
downloading_video
downloading_subtitle
translating_subtitle
rendering_subtitle
translating_title
uploading
uploaded
failed
```

---

## 4. 默认翻译配置

默认语言方向：

```yaml
translation:
  source_lang: en
  target_lang: zh-CN
```

默认 AI 配置：

```yaml
ai:
  provider: deepseek
  model: deepseek-v4-flash
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY
```

说明：

- 已通过当前 `.env` 中的 `DEEPSEEK_API_KEY` 检查 DeepSeek API。
- `https://api.deepseek.com` 可访问。
- 当前账号可用模型包含：`deepseek-v4-flash`、`deepseek-v4-pro`。
- `deepseek-v4-flash` 已通过 Chat Completions 测试，可作为默认模型。
- 注意：`deepseek-v4-flash` 会消耗 reasoning tokens，`max_tokens` 不能设置过小；标题/字幕翻译实现时应预留足够输出 token。

---

## 5. 字幕策略

默认字幕来源优先级：

```text
1. YouTube 官方英文字幕
2. YouTube 自动英文字幕
3. 如果没有英文字幕，则任务失败
```

---

## 6. 字幕压制策略

默认使用硬字幕，并采用参考图中的“双语底部字幕”样式：

```text
第一行：中文字幕，大字号、白字、粗黑描边、底部居中
第二行：英文原字幕，小字号、白字、黑描边、位于中文字幕下方
```

视觉目标：

```text
- 位置：画面底部居中，距离底边保留安全边距
- 中文：更醒目，粗体，白色填充，黑色粗描边
- 英文：保留原文，小一号，白色填充，黑色描边
- 字幕最多建议中文 1 行 + 英文 1~2 行
- 尽量避免遮挡关键画面，但优先保证移动端可读性
```

推荐用 ASS 字幕实现，而不是直接烧录 SRT，因为 ASS 可以精确控制双语样式、字号、描边、行距和边距。

示例 ASS 样式规划：

```text
Style: CN,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,4,0,2,60,60,82,1
Style: EN,Arial,30,&H00FFFFFF,&H000000FF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,42,1
```

说明：

```text
- CN 字号约 54，黑色描边 4
- EN 字号约 30，黑色描边 3
- 两条字幕均为底部居中 Alignment=2
- CN MarginV 大于 EN，使中文显示在英文上方
- EN MarginV 较小，使英文显示在更靠近底部的位置
```

实际实现时应根据视频分辨率动态缩放字号。例如以 1920x1080 为基准：

```text
中文字号 = 视频高度 * 0.050
英文字号 = 视频高度 * 0.028
中文底部边距 = 视频高度 * 0.076
英文底部边距 = 视频高度 * 0.039
```

压制命令规划：

```bash
ffmpeg -i input.mp4 -vf "ass=subtitle.bilingual.ass" -c:a copy output.mp4
```

规划新增服务：

```text
src/service/subtitle.py
src/service/renderer.py
src/infra/ffmpeg.py
```

其中：

- `subtitle.py`：负责字幕下载、格式转换、字幕分段翻译、生成双语 ASS 字幕
- `renderer.py`：负责把 ASS 字幕压制进视频
- `ffmpeg.py`：负责封装 ffmpeg 命令、获取视频分辨率和可用性检查

---

## 7. 新项目结构规划

建议结构：

```text
src/
  cli.py
  commands/
    login.py
    check.py
    translate.py
    jobs.py
    logs.py
  service/
    downloader.py
    subtitle.py
    renderer.py
    translator.py
    uploader.py
    pipeline.py
  infra/
    yt_dlp.py
    ffmpeg.py
    biliup.py
    ai_client.py
    cli_path.py
  config/
    config.py
    config.yaml
  logger.py
  state.py
```

需要删除或废弃：

```text
src/scheduler.py
src/service/monitor.py
src/infra/youtube_api.py  # 如果不再需要 YouTube Data API
main.py                  # 或改为兼容入口，调用 src.cli:main
```

`pyproject.toml` 增加：

```toml
[project.scripts]
y2b = "src.cli:main"
```

---

## 8. 配置文件规划

新的配置可以简化为：

```yaml
global:
  download_dir: ./downloads
  output_dir: ./output
  log_dir: ./logs
  state_db: ./data/state.db
  max_retry: 3

ai:
  provider: deepseek
  model: deepseek-v4-flash
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY

youtube:
  cookies: ./data/youtube_cookies.txt
  cookies_from_browser:
  extractor_args: []

translation:
  source_lang: en
  target_lang: zh-CN
  max_title_length: 70
  style_prompt: "适合B站的中文标题，简洁、自然、不夸张"
  glossary:
    Brawl Stars: 荒野乱斗
    Hypercharge: 终极技能

bilibili:
  cookies: ./data/bilibili_cookies.json
  executable: biliup
  user_cookie_arg: -u
  default_tags: ["搬运", "翻译"]
  default_tid: 4
  title_prefix: ""
  extra_args: []
  upload:
    copyright: 2
    source: youtube
    line: AUTO
```

---

## 9. 状态存储规划

新增 `jobs` 表：

```sql
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  video_id TEXT,
  url TEXT NOT NULL,
  title TEXT,
  translated_title TEXT,
  status TEXT NOT NULL,
  progress INTEGER DEFAULT 0,
  current_step TEXT,
  video_path TEXT,
  subtitle_path TEXT,
  rendered_path TEXT,
  bvid TEXT,
  error TEXT,
  created_at INTEGER,
  updated_at INTEGER
);
```

保留或改造原 `videos` 表：

- 如果只做单视频任务，可逐步合并到 `jobs` 表。
- 第一版可以保留 `videos` 表避免改动过大，但业务状态以 `jobs` 为主。

---

## 10. 实施阶段

### 阶段 1：CLI 基础与登录检查

目标命令：

```bash
y2b login youtube
y2b login bilibili
y2b check
```

任务：

- 新增 CLI 入口
- 增加 `pyproject.toml` scripts
- 封装 YouTube cookie 写入逻辑
- 封装 Bilibili 登录逻辑
- 检查 `yt-dlp` / `biliup` / `ffmpeg`
- 移除监控启动逻辑

---

### 阶段 2：单视频基础搬运

目标命令：

```bash
y2b translate <url>
```

先实现：

```text
拉元信息 -> 下载视频 -> 翻译标题 -> 上传 Bilibili
```

暂时可不处理字幕，确保单链接链路跑通。

---

### 阶段 3：字幕下载与翻译

目标：

```text
下载英文字幕 -> 翻译中文字幕 -> 输出 zh-CN.srt
```

任务：

- 扩展 `yt_dlp` 字幕下载能力
- 支持 `.vtt` 转 `.srt`
- 支持字幕分块翻译
- 支持术语表
- 支持失败重试

---

### 阶段 4：字幕压制

目标：

```text
ffmpeg 硬字幕压制 -> 生成最终上传视频
```

任务：

- 检查 ffmpeg 可用
- 实现字幕路径转义
- 生成 `output/<video_id>.mp4`
- 上传压制后视频

---

### 阶段 5：任务状态与日志

目标命令：

```bash
y2b jobs
y2b status <job_id>
y2b logs -f
```

任务：

- 新增 `jobs` 表
- 每个处理步骤更新状态
- 支持查看最近任务
- 支持查看单任务详情
- 支持实时日志

---

## 11. 待确认问题

实现前需要确认：

1. CLI 框架使用 `argparse`、`typer` 还是 `click`？
   - 推荐：`typer`
2. DeepSeek v4 flash 的官方模型 ID 是什么？
   - 已确认当前 API 返回模型 ID：`deepseek-v4-flash`
3. 没有英文字幕时是否直接失败？
   - 当前规划：直接失败
4. Bilibili 默认分区、默认标签是否使用当前配置？
5. 是否需要第一版支持后台执行 `--background`？
   - 当前建议：第一版前台执行，状态写入 SQLite；后续再做后台任务。
