import yaml
from dataclasses import dataclass
from pathlib import Path

@dataclass
class ChannelConfig:
    name: str
    yt_channel_id: str
    bili_tags: list
    bili_tid: int
    title_prefix: str
    enabled: bool = True

@dataclass
class AppConfig:
    poll_interval: int
    download_dir: str
    log_dir: str
    state_db: str
    channels: list

def load_config() -> AppConfig:
    with open(Path(__file__).parent / "config.yaml", "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    channels = [
        ChannelConfig(**c) for c in raw["channels"]
    ]

    return AppConfig(
        poll_interval=raw["global"]["poll_interval"],
        download_dir=raw["global"]["download_dir"],
        log_dir=raw["global"]["log_dir"],
        state_db=raw["global"]["state_db"],
        channels=channels
    )
