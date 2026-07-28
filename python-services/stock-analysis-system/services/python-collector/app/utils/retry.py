"""
统一接口重试工具

仅对可恢复异常进行有限次数重试（指数退避）：
    - 网络超时
    - 临时连接错误
    - 接口限流
    - 数据源临时不可用

不对以下错误重试：
    - Token 无效
    - 参数错误
    - 字段结构错误
"""

import time
import functools
from typing import Callable, TypeVar, Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')

# 可重试的异常关键字（匹配异常消息中的字符串）
_RETRYABLE_KEYWORDS = [
    'timeout',
    'timed out',
    'connection',
    'connectionerror',
    'connectionreset',
    'broken pipe',
    'temporary failure',
    'rate limit',
    'too many requests',
    '503',
    '502',
    '504',
    'temporarily unavailable',
]

# 不可重试的异常关键字
_NON_RETRYABLE_KEYWORDS = [
    'token',
    'invalid token',
    'unauthorized',
    'forbidden',
    '400',
    '401',
    'invalid parameter',
    'param error',
]


def is_retryable(exc: Exception) -> bool:
    """判断异常是否可重试。"""
    msg = str(exc).lower()

    # 不可重试优先判断
    for kw in _NON_RETRYABLE_KEYWORDS:
        if kw in msg:
            return False

    # 可重试判断
    for kw in _RETRYABLE_KEYWORDS:
        if kw in msg:
            return True

    # 默认允许重试（保守策略：未知异常尝试重试）
    return True


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_check: Callable[[Exception], bool] | None = None,
):
    """
    重试装饰器（指数退避）。

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟秒数
        retryable_check: 自定义可重试判断函数

    Usage::

        @retry_with_backoff(max_retries=3, base_delay=1.0)
        def fetch_data():
            ...
    """
    check_fn = retryable_check or is_retryable

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    if attempt >= max_retries:
                        logger.error(
                            f"[重试] {func.__name__} 第 {attempt}/{max_retries} 次失败: {e}，不再重试"
                        )
                        raise
                    if not check_fn(e):
                        logger.error(
                            f"[重试] {func.__name__} 不可重试异常: {e}"
                        )
                        raise
                    wait = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"[重试] {func.__name__} 第 {attempt}/{max_retries} 次失败: {e}，"
                        f"等待 {wait:.1f}s 后重试"
                    )
                    time.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator
