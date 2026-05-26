import hashlib
import json
import math

try:
    from modules.shared.config import redis_client
except ImportError:
    import redis
    redis_client = redis.Redis(host='localhost', port=6379, db=0)


class ABTester:
    def __init__(self):
        self.tests = {}
        self._load_tests()

    def _load_tests(self):
        try:
            keys = redis_client.keys("ab_test:*")
            for key in keys:
                data = json.loads(redis_client.get(key))
                name = key.decode().split(":", 1)[1]
                self.tests[name] = {
                    "a": data["a_config"], "b": data["b_config"],
                    "ac": data["a_success"], "at": data["a_total"],
                    "bc": data["b_success"], "bt": data["b_total"],
                    "b_prob": data.get("b_prob", 0.5),
                    "winner": data["winner"], "status": data["status"]
                }
        except Exception:
            pass

    def start(self, n: str, a: dict, b: dict, a_weight: float = 0.5, b_weight: float = 0.5):
        total = a_weight + b_weight
        b_prob = b_weight / total if total > 0 else 0.5
        self.tests[n] = {
            "a": a, "b": b,
            "ac": 0, "at": 0,
            "bc": 0, "bt": 0,
            "b_prob": b_prob,
            "winner": None, "status": "running"
        }
        self._update_redis(n)

    def assign(self, test_name: str, user_id: str) -> str:
        t = self.tests.get(test_name)
        if not t:
            return "a"
        if t["status"] == "completed" and t["winner"]:
            return t["winner"]
        if t["status"] != "running":
            return "a"
        h = int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 10000
        return "b" if h < t["b_prob"] * 10000 else "a"

    def record(self, n: str, v: str, ok: bool):
        t = self.tests.get(n)
        if not t or t["status"] != "running":
            return
        if v == "a":
            t["at"] += 1
            if ok:
                t["ac"] += 1
        elif v == "b":
            t["bt"] += 1
            if ok:
                t["bc"] += 1
        else:
            return
        self._update_redis(n)
        if t["at"] < 5 or t["bt"] < 5:
            return
        ac, at = t["ac"], t["at"]
        bc, bt = t["bc"], t["bt"]
        p1 = ac / at
        p2 = bc / bt
        p_pool = (ac + bc) / (at + bt)
        if p_pool in (0, 1):
            return
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / at + 1 / bt))
        if se == 0:
            return
        z = (p1 - p2) / se
        if abs(z) > 1.96:
            t["winner"] = "a" if p1 > p2 else "b"
            t["status"] = "completed"
            self._update_redis(n)

    def _update_redis(self, test_name: str):
        t = self.tests.get(test_name)
        if not t:
            return
        data = {
            "a_config": t["a"], "b_config": t["b"],
            "a_success": t["ac"], "a_total": t["at"],
            "a_rate": round(t["ac"] / t["at"], 4) if t["at"] > 0 else 0,
            "b_success": t["bc"], "b_total": t["bt"],
            "b_rate": round(t["bc"] / t["bt"], 4) if t["bt"] > 0 else 0,
            "b_prob": t.get("b_prob", 0.5),
            "winner": t["winner"],
            "status": t["status"]
        }
        try:
            redis_client.set(f"ab_test:{test_name}", json.dumps(data))
        except Exception:
            pass