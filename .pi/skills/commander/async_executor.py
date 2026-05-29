#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
AsyncExecutor — 统一的同步→异步桥接层
=========================================
解决 Yaxiio 项目中 Commander（同步主循环）与 LifecycleManager（异步）
之间的线程安全问题。

设计原则：
  1. 单例模式，全局只有一个 event loop
  2. 线程安全：所有跨线程调用走 run_coroutine_threadsafe
  3. 优雅关闭：shutdown() 等待所有 pending 任务完成
  4. 超时保护：每个调用都有可配置的超时时间

使用方式：
  from async_executor import async_executor

  # 同步代码中调用异步方法
  result = async_executor.run(commander.lifecycle.start())

  # 异步代码中直接 await（不需要桥接）
  await commander.lifecycle.evolve()

  # 关闭
  async_executor.shutdown()
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Optional

logger = logging.getLogger("yaxiio.async_executor")


class AsyncExecutor:
    """全局异步执行器单例。

    在后台线程中运行一个持久的 asyncio event loop，
    所有同步代码通过 run() 方法提交协程并等待结果。
    """

    DEFAULT_TIMEOUT = 30  # 默认超时秒数

    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._shutting_down = False
        self._pending_count = 0
        self._lock = threading.Lock()

    # ── 启动 ─────────────────────────────────────────────

    def start(self):
        """启动后台 event loop 线程。幂等调用。"""
        if self._started:
            return

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_forever,
            name="yaxiio-async",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("AsyncExecutor 已启动 (loop thread: %s)", self._thread.name)

    def _run_forever(self):
        """后台线程：运行 event loop。"""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        except Exception as e:
            logger.error("AsyncExecutor event loop 异常退出: %s", e)
        finally:
            # 清理 pending 任务
            self._cleanup_pending()

    def _cleanup_pending(self):
        """取消所有 pending 任务，防止资源泄漏。"""
        if not self._loop or self._loop.is_closed():
            return
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            logger.info("AsyncExecutor 清理了 %d 个 pending 任务", len(pending))

    # ── 同步调用 ─────────────────────────────────────────

    def run(self, coro, timeout: float = None) -> Any:
        """在后台 event loop 中执行协程，同步等待结果。

        Args:
            coro: 协程对象
            timeout: 超时秒数，默认 DEFAULT_TIMEOUT

        Returns:
            协程返回值

        Raises:
            RuntimeError: 执行器未启动
            TimeoutError: 超时
        """
        if not self._started or not self._loop or self._loop.is_closed():
            raise RuntimeError("AsyncExecutor 未启动，请先调用 start()")

        timeout = timeout or self.DEFAULT_TIMEOUT

        with self._lock:
            self._pending_count += 1

        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            logger.warning("AsyncExecutor 超时 (%.1fs)，取消任务", timeout)
            future.cancel()
            raise TimeoutError(f"协程执行超时 ({timeout}s)")
        finally:
            with self._lock:
                self._pending_count -= 1

    def submit(self, coro) -> asyncio.Future:
        """提交协程到后台 event loop，不等待结果（fire-and-forget）。

        Args:
            coro: 协程对象

        Returns:
            concurrent.futures.Future（可用于后续检查状态）

        Raises:
            RuntimeError: 执行器未启动
        """
        if not self._started or not self._loop or self._loop.is_closed():
            raise RuntimeError("AsyncExecutor 未启动，请先调用 start()")

        with self._lock:
            self._pending_count += 1

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)

        # 自动减少计数
        def _done_callback(f):
            with self._lock:
                self._pending_count -= 1

        future.add_done_callback(_done_callback)
        return future

    # ── 状态检查 ─────────────────────────────────────────

    @property
    def pending(self) -> int:
        """当前 pending 的协程数。"""
        return self._pending_count

    @property
    def healthy(self) -> bool:
        """执行器是否健康（已启动且 loop 在运行）。"""
        return (
            self._started
            and self._loop is not None
            and not self._loop.is_closed()
            and self._thread is not None
            and self._thread.is_alive()
        )

    def stats(self) -> dict:
        return {
            "healthy": self.healthy,
            "pending": self.pending,
            "started": self._started,
            "shutting_down": self._shutting_down,
            "thread_alive": self._thread.is_alive() if self._thread else False,
        }

    # ── 关闭 ─────────────────────────────────────────────

    def shutdown(self, timeout: float = 10):
        """优雅关闭：停止 event loop，等待线程退出。

        Args:
            timeout: 等待 pending 任务完成的秒数
        """
        if not self._started or not self._loop:
            return

        self._shutting_down = True
        logger.info("AsyncExecutor 正在关闭 (pending=%d)...", self._pending_count)

        # 等待 pending 任务
        waited = 0
        while self._pending_count > 0 and waited < timeout:
            time.sleep(0.1)
            waited += 0.1

        if self._pending_count > 0:
            logger.warning("AsyncExecutor 关闭时仍有 %d 个 pending 任务", self._pending_count)

        # 停止 loop
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("AsyncExecutor 后台线程未能在 5s 内退出")

        try:
            self._loop.close()
        except Exception:
            pass

        self._started = False
        logger.info("AsyncExecutor 已关闭")


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

async_executor = AsyncExecutor()
