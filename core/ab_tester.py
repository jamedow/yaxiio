#!/usr/bin/env python3
"""
优化四：A/B 测试自进化策略 — ABTester
=======================================
让调度官学会"做实验"：对任意策略参数（拆分粒度/排队策略/Agent选择）跑A/B测试，
24小时自动评估 → 优胜策略自动推广 → 劣汰策略自动废弃。

Constitution R1: 使用 commander:* 前缀，不删除历史记录（保留评测数据）。
"""

import json
import random
import time
from datetime import datetime, timedelta
from typing import Optional

import redis


class ABTester:
    """A/B 测试框架：提出 → 分流 → 记录 → 评估 → 推广/废弃"""

    TEST_DURATION_HOURS = 24     # 默认测试周期
    MIN_SAMPLE_PER_GROUP = 10    # 每组最少样本数
    SIGNIFICANCE_THRESHOLD = 1.1 # 提升10%以上才推广

    def __init__(self, redis_host: str = "127.0.0.1", redis_port: int = 6379,
                 redis_password: str = "Lt@114514!"):
        self.redis = redis.Redis(
            host=redis_host, port=redis_port,
            password=redis_password, decode_responses=True,
        )

    # ── 提出优化策略 ──────────────────────────────────────────

    def propose_optimization(self, strategy_name: str,
                             strategy_config: dict,
                             duration_hours: Optional[int] = None) -> dict:
        """启动新的 A/B 测试。

        Args:
            strategy_name: 新策略名称，如 "deep-split-v2"
            strategy_config: 策略配置 dict，含完整参数
            duration_hours: 测试周期（小时），默认 24
        """
        # 互斥：同时只能有一个活跃测试
        if self.redis.exists("commander:ab_test:active"):
            return {"status": "rejected", "reason": "已有进行中的 A/B 测试"}

        test_id = f"ab-{int(time.time())}"
        test_config = {
            "test_id": test_id,
            "strategy_name": strategy_name,
            "strategy_config": strategy_config,
            "start_time": datetime.now().isoformat(),
            "duration_hours": duration_hours or self.TEST_DURATION_HOURS,
            "group_a": {"name": "当前策略 (Control)", "success": 0, "total": 0},
            "group_b": {"name": strategy_name + " (Variant)", "success": 0, "total": 0},
            "status": "active",
        }

        self.redis.set("commander:ab_test:active",
                       json.dumps(test_config, ensure_ascii=False))
        return {"status": "started", "test_id": test_id}

    # ── 分流 ──────────────────────────────────────────────────

    def route_task(self, split_ratio: float = 0.5) -> str:
        """为新任务分配测试组。

        Returns:
            "group_a" (对照组) / "group_b" (实验组) / "default" (无测试)
        """
        if not self.redis.exists("commander:ab_test:active"):
            return "default"

        return "group_b" if random.random() < split_ratio else "group_a"

    # ── 记录结果 ─────────────────────────────────────────────

    def record_result(self, group: str, success: bool,
                      task_id: str = "", metadata: Optional[dict] = None):
        """记录单个任务执行结果到对应测试组。

        Args:
            group: "group_a" 或 "group_b"
            success: 是否成功
            task_id: 任务 ID（用于溯源）
            metadata: 附加信息（耗时、Agent 等）
        """
        raw = self.redis.get("commander:ab_test:active")
        if not raw:
            return

        test = json.loads(raw)
        if group not in test:
            return

        test[group]["total"] += 1
        if success:
            test[group]["success"] += 1

        # 记录明细到历史（保留完整数据，不删除）
        detail = {
            "test_id": test["test_id"],
            "group": group,
            "task_id": task_id,
            "success": success,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        self.redis.rpush(
            f"commander:ab_test:history:{test['test_id']}",
            json.dumps(detail, ensure_ascii=False),
        )
        # 历史记录保留 30 天
        self.redis.expire(f"commander:ab_test:history:{test['test_id']}", 86400 * 30)

        # 更新活跃测试状态
        self.redis.set("commander:ab_test:active",
                       json.dumps(test, ensure_ascii=False))

    # ── 评估与决策 ────────────────────────────────────────────

    def evaluate_and_decide(self) -> dict:
        """评估当前 A/B 测试，决定推广或废弃。"""
        raw = self.redis.get("commander:ab_test:active")
        if not raw:
            return {"status": "no_active_test"}

        test = json.loads(raw)

        # 时间门槛
        start_time = datetime.fromisoformat(test["start_time"])
        elapsed = (datetime.now() - start_time).total_seconds() / 3600

        if elapsed < test["duration_hours"]:
            return {
                "status": "still_testing",
                "elapsed_hours": round(elapsed, 1),
                "remaining_hours": round(test["duration_hours"] - elapsed, 1),
            }

        # 样本数门槛
        group_a = test["group_a"]
        group_b = test["group_b"]
        min_sample = self.MIN_SAMPLE_PER_GROUP

        if group_a["total"] < min_sample or group_b["total"] < min_sample:
            # 延长测试
            test["duration_hours"] += 12
            self.redis.set("commander:ab_test:active",
                           json.dumps(test, ensure_ascii=False))
            return {
                "status": "extended",
                "reason": f"样本不足 (A:{group_a['total']}, B:{group_b['total']} < {min_sample})",
                "new_duration_hours": test["duration_hours"],
            }

        # 计算成功率
        rate_a = group_a["success"] / max(group_a["total"], 1)
        rate_b = group_b["success"] / max(group_b["total"], 1)

        # 归档测试结果（不删除，永久保留）
        archive_key = f"commander:ab_test:archive:{test['test_id']}"
        test["status"] = "completed"
        test["result"] = {
            "rate_a": round(rate_a, 4),
            "rate_b": round(rate_b, 4),
            "samples_a": group_a["total"],
            "samples_b": group_b["total"],
            "evaluated_at": datetime.now().isoformat(),
        }
        self.redis.set(archive_key, json.dumps(test, ensure_ascii=False))

        # 决策
        if rate_b > rate_a * self.SIGNIFICANCE_THRESHOLD:
            improvement = (rate_b - rate_a) / max(rate_a, 0.001) * 100
            self._promote_strategy(test)

            # 活跃测试标记为非活跃（用 EXPIRE 自动过期代替 DEL）
            self.redis.rename("commander:ab_test:active",
                              f"commander:ab_test:completed:{test['test_id']}")
            self.redis.expire(f"commander:ab_test:completed:{test['test_id']}", 3600)

            return {
                "decision": "promote",
                "rate_a": round(rate_a, 4),
                "rate_b": round(rate_b, 4),
                "improvement": f"{improvement:.1f}%",
                "message": (
                    f"新策略「{test['strategy_name']}」胜出 "
                    f"(A:{rate_a:.1%} → B:{rate_b:.1%})，已自动推广"
                ),
            }
        else:
            # 废弃新策略
            self.redis.rename("commander:ab_test:active",
                              f"commander:ab_test:completed:{test['test_id']}")
            self.redis.expire(f"commander:ab_test:completed:{test['test_id']}", 3600)

            return {
                "decision": "discard",
                "rate_a": round(rate_a, 4),
                "rate_b": round(rate_b, 4),
                "message": (
                    f"新策略「{test['strategy_name']}」未显著提升 "
                    f"(A:{rate_a:.1%} vs B:{rate_b:.1%})，已自动废弃"
                ),
            }

    # ── 推广新策略 ───────────────────────────────────────────

    def _promote_strategy(self, test_config: dict):
        """将优胜策略写入当前调度配置（Redis + 持久化提示）。"""
        strategy_name = test_config["strategy_name"]
        strategy_config = test_config["strategy_config"]

        # 写入 Redis 作为当前活跃策略
        policy = {
            "name": strategy_name,
            "config": strategy_config,
            "promoted_at": datetime.now().isoformat(),
            "test_id": test_config["test_id"],
        }
        self.redis.set("commander:agent:scheduling_policy",
                       json.dumps(policy, ensure_ascii=False))

        # 记录推广历史
        self.redis.rpush(
            "commander:ab_test:promotions",
            json.dumps(policy, ensure_ascii=False),
        )

    # ── 工具方法 ─────────────────────────────────────────────

    def get_active_test(self) -> Optional[dict]:
        """获取当前活跃的 A/B 测试。"""
        raw = self.redis.get("commander:ab_test:active")
        return json.loads(raw) if raw else None

    def get_promotion_history(self, limit: int = 10) -> list:
        """获取策略推广历史。"""
        items = self.redis.lrange("commander:ab_test:promotions", -limit, -1)
        return [json.loads(item) for item in items]

    def get_current_policy(self) -> Optional[dict]:
        """获取当前生效的调度策略。"""
        raw = self.redis.get("commander:agent:scheduling_policy")
        return json.loads(raw) if raw else None
