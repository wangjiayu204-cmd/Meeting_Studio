import logging


def configure_logging(level: str) -> logging.Logger:
    """只配置项目 logger；多次创建应用不累加 handler。"""
    logger = logging.getLogger("meeting_pm")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
