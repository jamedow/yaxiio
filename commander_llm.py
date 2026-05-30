"""
Commander LLM Manager — LLM 客户端管理
========================================
从 yaxiio.Commander._get_llm() 提取。
"""
import os, sys


def get_llm_client(redis_client, workflow, task_type="default", task_desc=""):
    """
    获取 LLM 客户端 + 防呆降级。
    
    Args:
        redis_client: Commander 的 Redis 客户端
        workflow: WorkflowEngine 实例（用于模型路由）
        task_type: 任务类型
        task_desc: 任务描述（用于智能模型选择）
    
    Returns:
        LLMAdapter 实例，或 None
    """
    try:
        sys.path.insert(0, "/app/.pi/skills/commander")
        from agent_lifecycle_v2 import LLMAdapter
        
        # Try IntelligentModelRouter first
        model = task_type
        thinking = "medium"
        
        if workflow and hasattr(workflow, 'model_router_v2') and workflow.model_router_v2:
            router = workflow.model_router_v2
            task_info = {"action": task_type, "description": task_desc or task_type}
            cfg = router.select(task_info)
            model = cfg.get("model", task_type)
            thinking = cfg.get("thinking", "medium")
            print("[Commander] model router: {} (thinking={}, score={})".format(
                model, thinking, cfg.get("score", 0)), flush=True)
        
        key = redis_client.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        return LLMAdapter(api_key=key, base_url="https://api.deepseek.com/v1",
                        model=model, thinking=thinking)
    except Exception as e:
        # Fool-proof: fallback to default
        print("[Commander] LLM init failed ({}), fallback to default".format(e), flush=True)
        try:
            from agent_lifecycle_v2 import LLMAdapter
            key = redis_client.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
            return LLMAdapter(api_key=key, base_url="https://api.deepseek.com/v1",
                            model="deepseek-chat", thinking="medium")
        except:
            return None
