import json, time, queue, threading
class MCPStreamWriter:
    def __init__(self): self._q=queue.Queue(); self._closed=False
    def write(self,e,d): self._q.put({"event":e,"data":d,"time":time.time()})
    def close(self): self._closed=True; self._q.put(None)
    def generator(self):
        while True:
            item=self._q.get()
            if item is None: break
            yield f"event: {item[\"event\"]}\ndata: {json.dumps(item[\"data\"],ensure_ascii=False)}\n\n"
class MCPStreamServer:
    def __init__(self,cmdr=None): self.cmdr=cmdr
    def execute_streaming(self,tid,action,payload):
        w=MCPStreamWriter()
        def run():
            w.write("status",{"tid":tid,"status":"started"})
            try:
                if self.cmdr:
                    if action=="site_audit": r=self.cmdr._run_audit(tid,payload)
                    else: r=self.cmdr._run_diagnose(tid,payload)
                    w.write("result",{"tid":tid,"status":"completed","result":r})
            except Exception as e: w.write("error",{"msg":str(e)})
            w.close()
        threading.Thread(target=run,daemon=True).start()
        return w
