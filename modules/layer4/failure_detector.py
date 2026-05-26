"""失败检测器（增强版：滑动窗口、故障分类、自动修复）"""

import time
from enum import Enum
from typing import Dict, List, Tuple, Optional

class FailureCategory(Enum):
    TRANSIENT = "瞬时故障"
    PERSISTENT = "持续故障"

class RecoveryAction(Enum):
    RESTART = "重启"
    REBUILD = "重建"
    DEGRADE = "降级"
    NONE = "无动作"

class FailureDetector:
    def __init__(self):
        self.failures: Dict[str, List[Tuple[float, str]]] = {}
        self.window = 300          # 5分钟滑动窗口
        self.persistent_threshold = 0.6
        self.last_action: Dict[str, Tuple[float, RecoveryAction]] = {}
        self.cooldown = 60         # 动作冷却时间（秒）

    def record(self, aid: str, err: str):
        now = time.time()
        self.failures.setdefault(aid, []).append((now, err))
        self._prune(aid, now)
        self._auto_recover(aid, now)

    def should_restart(self, aid: str, mx: int = 3) -> bool:
        now = time.time()
        self._prune(aid, now)
        return len(self.failures.get(aid, [])) >= mx

    def diagnose(self, aid: str) -> Tuple[FailureCategory, RecoveryAction, str]:
        now = time.time()
        self._prune(aid, now)
        records = self.failures.get(aid, [])
        if not records:
            return FailureCategory.TRANSIENT, RecoveryAction.NONE, "无故障记录"
        cat, action = self._analyze(records, now)
        detail = f"窗口内故障{len(records)}次"
        return cat, action, detail

    # ---------- 内部逻辑 ----------
    def _prune(self, aid: str, now: float):
        if aid not in self.failures:
            return
        self.failures[aid] = [
            (t, e) for t, e in self.failures[aid]
            if now - t <= self.window
        ]
        if not self.failures[aid]:
            del self.failures[aid]

    def _analyze(self, records: List[Tuple[float, str]], now: float
                 ) -> Tuple[FailureCategory, RecoveryAction]:
        count = len(records)
        if count < 3:
            return FailureCategory.TRANSIENT, RecoveryAction.NONE

        timestamps = [t for t, _ in records]
        recent_60s = sum(1 for t in timestamps if now - t <= 60)
        density = recent_60s / count

        if density >= self.persistent_threshold:
            cat = FailureCategory.PERSISTENT
            action = RecoveryAction.REBUILD if count >= 5 else RecoveryAction.RESTART
        else:
            cat = FailureCategory.PERSISTENT  # 分散但仍持续不稳定
            action = RecoveryAction.DEGRADE if count >= 5 else RecoveryAction.RESTART
        return cat, action

    def _auto_recover(self, aid: str, now: float):
        records = self.failures.get(aid, [])
        if not records:
            return
        _, action = self._analyze(records, now)
        if action != RecoveryAction.NONE:
            self._trigger_recovery(aid, action, now)

    def _trigger_recovery(self, aid: str, action: RecoveryAction, now: float):
        # 冷却检查，避免短时间内重复触发同一动作
        if aid in self.last_action:
            last_time, last_action = self.last_action[aid]
            if last_action == action and now - last_time < self.cooldown:
                return
        self.last_action[aid] = (now, action)

        print(f"[Recovery] 对 {aid} 触发 {action.value}")
        # 实际环境中应调用具体的修复模块，例如：
        # from .recovery_handler import restart_service, rebuild_service, degrade_service
        # 这里用模拟动作代替
        if action == RecoveryAction.RESTART:
            self._do_restart(aid)
        elif action == RecoveryAction.REBUILD:
            self._do_rebuild(aid)
        elif action == RecoveryAction.DEGRADE:
            self._do_degrade(aid)

    def _do_restart(self, aid: str):
        # 实际实现：subprocess 或 API 调用
        print(f"  执行重启: {aid}")

    def _do_rebuild(self, aid: str):
        print(f"  执行重建: {aid}")

    def _do_degrade(self, aid: str):
        print(f"  执行降级: {aid}")