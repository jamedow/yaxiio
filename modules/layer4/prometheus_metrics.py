import time
_start=time.time()
_M={"yaxiio_tasks_total":0,"yaxiio_tasks_failed":0,"yaxiio_llm_calls":0,"yaxiio_llm_tokens":0,"yaxiio_agents":0}
def update(k,d=1):
    if k in _M: _M[k]+=d
def snapshot():
    _M["yaxiio_uptime_seconds"]=int(time.time()-_start)
    return dict(_M)
def endpoint():
    lines=[]
    for k,v in snapshot().items(): lines.append(f"# HELP {k} Yaxiio metric"); lines.append(f"# TYPE {k} counter"); lines.append(f"{k} {v}")
    return "\n".join(lines)+"\n"
