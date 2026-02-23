# y2b（YouTube 自动搬运到 Bilibili）

一个用于监控指定 YouTube 频道、发现新视频后自动下载、翻译标题并上传到 Bilibili 的脚本。

当前特性（已实现）：
- 监控多个指定频道
- 仅处理程序启动后发布的视频（按启动时间过滤）
- 自动过滤：`Shorts`、直播中、直播预约、非公开/未发布内容
- 英文标题自动翻译（支持术语表）
- 状态持久化（SQLite），避免重复搬运
- 启动时自动检查 `yt-dlp` / `biliup`，并引导登录

## 1. 环境要求

- Python `3.12+`
- 可联网环境（访问 YouTube / B站 / AI 翻译接口）
- Bilibili 账号（用于 `biliup` 登录）
- DeepSeek API Key（默认）

## 2. 安装依赖（本地 / Linux 通用）

```bash
python -m venv .venv
```

Windows:
```powershell
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .
```

Linux (Ubuntu 24):
```bash
source .venv/bin/activate
pip install -U pip
pip install -e .
```

说明：
- 程序启动时会自动检查并尝试安装 `yt-dlp`、`biliup`
- 但生产环境建议你提前安装好，避免运行时临时安装失败

## 3. 配置

主要配置文件：`src/config/config.yaml`

你至少需要确认这些项：
- `global.poll_interval`: 轮询间隔（秒）
- `global.monitor_scan_limit`: 每轮每个频道扫描多少条（建议 20~100）
- `global.youtube`: YouTube 认证方式（cookies 文件或浏览器 cookies）
- `bilibili.cookies`: B站 cookies 文件保存路径（默认 `./cookies.json`）
- `channels`: 要监控的频道列表

### 翻译 API Key

在项目根目录创建 `.env`（或编辑已有文件）：

```env
DEEPSEEK_API_KEY=你的key
```

## 4. 首次启动（推荐先在本地交互式终端完成）

启动命令：

Windows:
```powershell
& .\.venv\Scripts\python.exe .\main.py
```

Linux:
```bash
./.venv/bin/python ./main.py
```

首次启动会自动执行：
- 检查 `yt-dlp` 是否可用
- 检查 `biliup` 是否可用
- 检查 YouTube 认证（会做真实探针，不只是判断文件存在）
- 若缺少 B站 cookies，会调用 `biliup login` 引导扫码登录

### YouTube 认证（两种方式，推荐浏览器 cookies）

配置在 `src/config/config.yaml` 的 `global.youtube`：

方式 A：使用导出的 cookies 文件（默认）
```yaml
global:
  youtube:
    cookies: ./www.youtube.com_cookies.txt
    # cookies_from_browser:
```

方式 B：直接读取浏览器 cookies（更稳，推荐）
```yaml
global:
  youtube:
    cookies_from_browser: edge
```

可选值示例（取决于你机器实际安装的浏览器）：
- `edge`
- `chrome`
- `chromium`
- `firefox`

说明：
- 如果遇到 `Sign in to confirm you're not a bot`，优先改用 `cookies_from_browser`
- 如果服务器是纯无头环境（没有桌面浏览器），建议在本地导出 `www.youtube.com_cookies.txt` 后上传到服务器

## 5. 快速测试（单频道最新视频）

用于验证“下载 -> 翻译 -> 上传”流程是否通（不受“启动后发布”限制）：

```bash
./.venv/bin/python ./test.py <YouTube频道ID>
```

Windows 示例：
```powershell
& .\.venv\Scripts\python.exe .\test.py UCU1Ag9C12YZFKY2HpNJr_3Q
```

## 6. Linux 服务器（Ubuntu 24）后台启动与长期运行

建议使用 `systemd`（比 `nohup`/`tmux` 更稳定）。

### 6.1 部署前建议（重要）

先在可交互终端完成一次启动，确保以下文件已经准备好：
- `.env`（含 `DEEPSEEK_API_KEY`）
- `cookies.json`（B站登录成功后生成）
- `www.youtube.com_cookies.txt`（如果你不用 `cookies_from_browser`）

如果服务器是无交互环境（如纯 SSH + service），程序在缺少认证文件时会直接报错退出，不会卡死等待输入。

### 6.2 `systemd` 服务文件（推荐）

创建文件 `/etc/systemd/system/y2b.service`：

```ini
[Unit]
Description=YouTube to Bilibili Repost Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/y2b
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/y2b/.venv/bin/python /opt/y2b/main.py
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

请按你的实际环境修改：
- `User=ubuntu`
- `WorkingDirectory=/opt/y2b`
- `ExecStart=/opt/y2b/.venv/bin/python /opt/y2b/main.py`

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable y2b
sudo systemctl start y2b
```

查看状态：

```bash
sudo systemctl status y2b
```

实时日志：

```bash
sudo journalctl -u y2b -f
```

重启服务（改完配置/代码后）：

```bash
sudo systemctl restart y2b
```

停止服务：

```bash
sudo systemctl stop y2b
```

### 6.3 `tmux` / `screen`（临时方案）

适合调试，不建议长期替代 `systemd`。

`tmux` 示例：
```bash
tmux new -s y2b
cd /opt/y2b
source .venv/bin/activate
python main.py
```

分离会话：按 `Ctrl+b`，再按 `d`

重新进入：
```bash
tmux attach -t y2b
```

### 6.4 `nohup`（临时方案）

```bash
cd /opt/y2b
nohup ./.venv/bin/python ./main.py >> run.log 2>&1 &
```

查看进程：
```bash
ps -ef | grep main.py
```

不建议长期用 `nohup` 的原因：
- 进程异常退出不会自动重启
- 运维能力弱于 `systemd`

## 7. 常见问题（FAQ）

### Q1: 日志出现 `Sign in to confirm you're not a bot`

说明 YouTube 认证失败（cookies 无效/过期/格式不对/风控）。

优先处理顺序：
1. 改用 `global.youtube.cookies_from_browser: edge`（或 `chrome/chromium/firefox`）
2. 重新导出 `www.youtube.com_cookies.txt`（Netscape 格式）
3. 在浏览器先访问 YouTube 和目标频道 `/videos` 页后再导出

### Q2: 程序启动后为什么不搬运旧视频？

这是设计行为：程序只处理“本次启动后发布”的视频，避免首次启动时回补大量历史视频。

### Q3: 如何确认程序正在正常轮询？

你会在日志中持续看到类似信息：
- `启动完成，仅处理发布时间晚于启动时间的视频...`
- `轮询结束，等待 xx 秒...`

## 8. 建议的生产实践

- 使用 `systemd` 长期运行
- 定期更新 `yt-dlp`
- 定期刷新 YouTube cookies（尤其遇到风控时）
- 将 `src/config/config.yaml` 和 cookies 做备份
- 先用 `test.py` 验证上传链路，再开长期监控
