"""
L4 Execution Handler — 执行层纯函数 + 委托封装
===============================================
从 workflow_engine._wait_for_neuron_response() 提取。

纯函数: wait_for_neuron(commander_redis, task_id, ...)
处理器: L4Handler 封装 Neuron 响应等待逻辑
"""
import json, time, os


def wait_for_neuron_response(redis_client, task_id: str, agent_name: str,
                              timeout: int = 120) -> dict:
    """
    等待 Neuron 通过 Redis Pub/Sub 返回结果 — 纯函数。
    
    Args:
        redis_client: Commander 的 Redis 客户端
        task_id: 父任务 ID
        agent_name: 目标 Agent 名称
        timeout: 超时秒数
    
    Returns:
        {"status": "success"/"error"/"timeout", "stdout": str, "elapsed_ms": int}
    
    无副作用，可独立测试。
    """
    if not redis_client:
        return {"status": "error", "error": "无法连接 Redis 等待响应"}
    
    print(f"[WF] 等待 {agent_name} 响应 (task={task_id}, timeout={timeout}s)...", flush=True)
    start = time.time()
    
    try:
        pubsub = redis_client.pubsub()
        pubsub.subscribe("lightingmetal:agent:commander")
        
        while time.time() - start < timeout:
            msg = pubsub.get_message(timeout=1.0)
            if not msg or msg["type"] != "message":
                continue
            
            try:
                data = json.loads(msg["data"])
            except json.JSONDecodeError:
                continue
            
            if data.get("taskId") == task_id and data.get("type") == "response":
                payload = data.get("payload", {})
                elapsed = time.time() - start
                print(f"[WF] {agent_name} 响应收到 (耗时 {elapsed:.1f}s)", flush=True)
                return {
                    "agent_id": agent_name,
                    "status": payload.get("status", "unknown"),
                    "stdout": str(payload.get("thought", payload.get("result", "")))[:5000],
                    "stderr": "",
                    "exit_code": 0,
                    "elapsed_ms": int(elapsed * 1000),
                }
        
        pubsub.close()
    except Exception as e:
        return {"status": "error", "error": f"等待 neuron 响应异常: {str(e)[:200]}"}
    
    return {"status": "timeout", "error": f"{agent_name} 未在 {timeout}s 内响应"}
