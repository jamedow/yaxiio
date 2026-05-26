import os, json, time
class CheckpointManager:
    def __init__(self,p="/opt/commander/data/checkpoints"): self.p=p; os.makedirs(p,exist_ok=True)
    def save(self,tid,state): 
        state["_ckpt"]=time.time()
        with open(f"{self.p}/{tid}.json","w") as f: json.dump(state,f,ensure_ascii=False)
    def load(self,tid):
        fp=f"{self.p}/{tid}.json"
        return json.load(open(fp)) if os.path.exists(fp) else None
    def delete(self,tid):
        fp=f"{self.p}/{tid}.json"
        if os.path.exists(fp): os.remove(fp)
    def list_all(self): return [f.replace(".json","") for f in os.listdir(self.p) if f.endswith(".json")]
