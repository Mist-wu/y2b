import signal
import time

from src.bootstrap import prepare_runtime
from src.config.config import load_config
from src.logger import setup_logger
from src.state import StateRepository
from src.scheduler import Scheduler

def main():
    startup_cutoff_ts = int(time.time())
    config = load_config()
    logger = setup_logger(config.log_dir)
    prepare_runtime(config, logger)
    state = StateRepository(config.state_db)

    scheduler = Scheduler(config, logger, state, startup_cutoff_ts=startup_cutoff_ts)
    signal_count = {"count": 0}

    def _handle_signal(signum, _frame):
        signal_count["count"] += 1
        if signal_count["count"] == 1:
            scheduler.request_stop(
                f"收到停止信号({signum})，正在安全退出... 再次按 Ctrl+C 可强制中断。"
            )
            return
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            # 非主线程或当前平台不支持时，退回默认行为
            pass

    try:
        scheduler.run()
    except KeyboardInterrupt:
        logger.warning("已强制中断退出。")

if __name__ == "__main__":
    main()
