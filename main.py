#加载配置
#初始化 logger / state
#启动 scheduler
from src.config.config import load_config
from src.logger import setup_logger
from src.state import StateRepository
from src.scheduler import Scheduler

def main():
    config = load_config()
    logger = setup_logger(config.log_dir)
    state = StateRepository(config.state_db)

    scheduler = Scheduler(config, logger, state)
    scheduler.run()

if __name__ == "__main__":
    main()
