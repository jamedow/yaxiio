# 结构化日志系统

## 格式

```
[时间] 级别 [trace_id] [模块] [方法] 操作 | key=value ...
```

## 示例

```
[22:36:18.503] INFO  [a1b2c3d4e5f6] [Commander] [handle_task] 宪法审查 | action=audit verdict=DELEGATED
[22:36:18.825] INFO  [a1b2c3d4e5f6] [WorkflowEngine] [process] L1感知完成 | intent=audit confidence=0.85
[22:36:19.102] INFO  [a1b2c3d4e5f6] [Commander] [spawn_neuron] Agent启动 | agent=审计官 model=deepseek-chat
[22:36:22.456] INFO  [a1b2c3d4e5f6] [Neuron] [think_and_act] 收到任务 | action=audit task_id=task-123
[22:36:25.789] ERROR [a1b2c3d4e5f6] [Commander] [handle_task] 任务异常 | error=Connection refused
```

## 使用

```python
from trace_logger import TraceLogger
log = TraceLogger("MyModule")

log.info("method_name", "操作描述", trace_id="abc123",
         key1="val1", key2=42, success=True)

log.warn("method_name", "警告描述", trace_id="abc123",
         reason="超时", threshold=100)

log.error("method_name", "错误描述", trace_id="abc123",
         error=str(e), code=500)
```

## 存储

- stdout: 实时输出 (Docker logs / PM2 logs)
- Redis: `trace:{trace_id}:log` (List, 最近 200 条, TTL 7 天)

## 查询

```python
from trace_logger import query_trace_logs
logs = query_trace_logs("a1b2c3d4")
# HTTP: GET /trace/a1b2c3d4
```
