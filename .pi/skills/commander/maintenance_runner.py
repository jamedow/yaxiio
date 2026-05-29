"""
MaintenanceRunner — 替代 CommanderV2 的周期性维护任务
=====================================================
Phase 2 架构统一: gateway.py 不再持有完整的 CommanderV2 实例，
而是通过 MaintenanceRunner 执行 A/B 测试评估、失联检测、自我进化等周期性任务。

与 CommanderV2 的关键区别:
- 不订阅 Redis Pub/Sub (yaxiio.py Commander 是唯一的任务处理器)
- 不处理任务消息
- 只执行定时维护操作
"""

import json, time, os


class MaintenanceRunner:
    """轻量级周期性维护任务执行器"""

    def __init__(self, redis_host="127.0.0.1", redis_port=6379, redis_password=""):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_password = redis_password
        self._start_time = time.time()

    def get_status(self) -> dict:
        """获取维护器状态 (兼容旧 CommanderV2.get_status 接口)"""
        try:
            import redis as _r
            r = _r.Redis(
                host=self.redis_host, port=self.redis_port,
                password=self.redis_password,
                decode_responses=True, socket_connect_timeout=3,
            )
            lock_owner = r.get("yaxiio:commander:lock")
            return {
                "commander_alive": bool(lock_owner),
                "commander_pid": lock_owner or "",
                "maintenance_uptime": int(time.time() - self._start_time),
            }
        except Exception:
            return {"commander_alive": False, "error": "Redis unreachable"}

    def run_daily_evaluation(self):
        """每日评估: 读取 Redis 中的 L5 评分统计 + A/B 测试结果"""
        try:
            import redis as _r
            r = _r.Redis(
                host=self.redis_host, port=self.redis_port,
                password=self.redis_password,
                decode_responses=True, socket_connect_timeout=3,
            )

            # 读取 A/B 测试状态
            ab_status = r.get("yaxiio:ab:status")
            if ab_status:
                ab_data = json.loads(ab_status)
                print(f"[Maintenance] A/B 测试状态: {ab_data.get(status, unknown)}")

            # 读取失联 Agent
            dead_agents = []
            for key in r.scan_iter("commander:agent:heartbeat:*"):
                hb = r.get(key)
                if hb:
                    agent = key.decode() if isinstance(key, bytes) else key
                    agent = agent.replace("commander:agent:heartbeat:", "")
                    try:
                        age = time.time() - float(hb)
                        if age > 120:
                            dead_agents.append(agent)
                    except ValueError:
                        pass

            if dead_agents:
                print(f"[Maintenance] 失联 Agent: {dead_agents}")

            return {
                "ab_test": ab_status,
                "dead_agents": dead_agents,
                "timestamp": time.time(),
            }
        except Exception as e:
            print(f"[Maintenance] 评估异常: {e}")
            return {"error": str(e)[:200]}

    def shutdown(self):
        """优雅关闭 (轻量级，无复杂资源需释放)"""
        print("[Maintenance] 关闭维护运行器")
