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
    scheduler.run()

if __name__ == "__main__":
    main()
