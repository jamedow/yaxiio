"""
Yaxiio 全链路断言 & 健康检查
=============================
在关键节点自动检测异常，打印明确告警。

设计原则:
  - 非侵入: 断言失败不阻断流程，只告警
  - 可观测: 每个断言输出明确消息 + 写入 Redis
  - 分层: L1→L5 各层独立检查

断言点:
  1. 入口: API Key 有效性
  2. L3: Stream 积压阈值
  3. L4: Neuron 存活 + 进度
  4. L5: 评分合理性
  5. GC: 清理效果
"""

import json, os, time

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")


def _r():
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS,
                       decode_responses=True, socket_connect_timeout=3)


def _alert(msg: str, level: str = "WARN"):
    """输出告警并写入 Redis"""
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    try:
        r = _r()
        r.lpush("yaxiio:alerts", json.dumps({"ts": ts, "level": level, "msg": msg}))
        r.ltrim("yaxiio:alerts", 0, 99)
    except Exception:
        pass


# ═══════════════════════════════════════════════
# 断言函数
# ═══════════════════════════════════════════════

def assert_api_key():
    """检查 API Key 是否可用"""
    try:
        r = _r()
        key = r.get("yaxiio:config:llm_api_key")
        if not key:
            _alert("🚨 API Key 为空! Neuron 将无法工作. 请设置 yaxiio:config:llm_api_key", "CRITICAL")
            return False
        # 快速验证
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1", timeout=5)
            resp = client.chat.completions.create(model="deepseek-chat",
                messages=[{"role": "user", "content": "1"}], max_tokens=1)
            if not resp.choices[0].message.content:
                raise Exception("empty response")
        except Exception as e:
            _alert(f"🚨 API Key 验证失败: {str(e)[:100]}", "CRITICAL")
            return False
        return True
    except Exception as e:
        _alert(f"API Key 检查异常: {e}", "ERROR")
        return False


def assert_stream_health(max_pending: int = 500):
    """检查 Stream 积压是否超标"""
    try:
        r = _r()
        for stream, group in [("yaxiio:stream:L4", "agents-L4")]:
            try:
                pending_info = r.xpending(stream, group)
                pending = pending_info.get("pending", 0) if isinstance(pending_info, dict) else 0
                length = r.xlen(stream)
                if pending > max_pending:
                    _alert(f"⚠️ {stream} 积压: {pending}/{length}, 超过阈值 {max_pending}", "WARN")
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return True  # Redis 不可达不算断言失败


def assert_neuron_alive(agent_name: str = "审计官", max_idle_sec: int = 300):
    """检查 Neuron 是否存活"""
    try:
        r = _r()
        state_raw = r.get(f"agent:{agent_name}:state")
        if not state_raw:
            _alert(f"⚠️ Neuron {agent_name} 无状态记录", "WARN")
            return False
        state = json.loads(state_raw)
        since = state.get("since", 0)
        idle_sec = time.time() - since
        if state.get("state") == "FAULT":
            _alert(f"🚨 Neuron {agent_name} 进入 FAULT 状态", "CRITICAL")
            return False
        if idle_sec > max_idle_sec and state.get("state") in ("EXECUTING",):
            _alert(f"⚠️ Neuron {agent_name} 执行中超过 {max_idle_sec}s", "WARN")
        return True
    except Exception:
        return True


def assert_score_reasonable(score: float, output_len: int):
    """检查评分是否合理"""
    if output_len > 500 and score < 3:
        _alert(f"⚠️ 评分异常: 输出{output_len}字但只有{score}分", "WARN")
        return False
    if output_len < 50 and score > 8:
        _alert(f"⚠️ 评分虚高: 输出仅{output_len}字却有{score}分", "WARN")
        return False
    return True


def assert_gc_effective(min_cleaned_per_run: int = 1):
    """检查 GC 是否在有效清理"""
    try:
        r = _r()
        raw = r.get("yaxiio:gc:stats")
        if raw:
            stats = json.loads(raw)
            total = stats.get("stream_acked", 0) + stats.get("tasks_deleted", 0)
            if stats.get("runs", 0) > 5 and total == 0:
                _alert("⚠️ GC 运行多次但未清理任何资源", "WARN")
                return False
        return True
    except Exception:
        return True


# ═══════════════════════════════════════════════
# 全量健康检查
# ═══════════════════════════════════════════════

def full_health_check() -> dict:
    """执行全链路健康检查, 返回通过/失败统计"""
    results = {}

    results["api_key"] = assert_api_key()
    results["stream"] = assert_stream_health()
    results["neuron"] = assert_neuron_alive()
    results["gc"] = assert_gc_effective()

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    all_ok = passed == total

    status = "✅" if all_ok else f"⚠️ {passed}/{total}"
    _alert(f"健康检查: {status} " + " ".join(f"{k}={v}" for k, v in results.items()))

    return {"passed": passed, "total": total, "all_ok": all_ok, "details": results}


# 快捷入口
if __name__ == "__main__":
    full_health_check()
