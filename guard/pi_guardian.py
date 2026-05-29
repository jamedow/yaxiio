#!/usr/bin/env python3
"""
雅溪双守护 v2.1 — AI诊断 + 互保 + 共同看守
=============================================
Core崩溃时:
  1. 读 PM2 error log
  2. LLM 分析根因并生成修复
  3. 应用修复 → 重启
  4. 如果 LLM 不可用，fallback 到备份恢复

配置:
  GUARDIAN_ID=1|2  (两个实例)
  DEEPSEEK_API_KEY=sk-xxx
"""

import json, os, signal, subprocess, time, threading

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")
GUARDIAN_ID = os.environ.get("GUARDIAN_ID", "1")
PARTNER_ID = "2" if GUARDIAN_ID == "1" else "1"
LLM_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

CORE_FILE = "/app/.pi/skills/commander/commander_v2.py"
BACKUP_FILE = "/tmp/v2.bak"
CORE_NAME = "yaxiio-core"

HEARTBEAT_KEY = f"yaxiio:guardian:{GUARDIAN_ID}:heartbeat"
PARTNER_KEY = f"yaxiio:guardian:{PARTNER_ID}:heartbeat"

try:
    import redis as redis_lib
    r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS or None, decode_responses=True)
    r.ping()
    HAS_REDIS = True
except:
    r = None; HAS_REDIS = False

def log(msg):
    print(f"[Guardian-{GUARDIAN_ID}] {time.strftime('%H:%M:%S')} {msg}", flush=True)

def pm2_status(name):
    try:
        result = subprocess.run(["pm2","jlist"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            for p in json.loads(result.stdout):
                if name in p.get("name",""):
                    s = p.get("pm2_env",{}).get("status")
                    rs = p.get("pm2_env",{}).get("restart_time", 0)
                    return s, rs
    except: pass
    return None, 0

def pm2_restart(name):
    subprocess.run(["pm2","restart",name], capture_output=True, timeout=10)

def pm2_logs(name, lines=30):
    try:
        r = subprocess.run(["pm2","logs",name,"--lines",str(lines),"--nostream"], 
                          capture_output=True, text=True, timeout=5)
        return (r.stdout + r.stderr)[:3000]
    except: return ""

def get_error_log():
    try:
        with open(f"/root/.pm2/logs/{CORE_NAME}-error.log") as f:
            lines = f.readlines()
            return "".join(lines[-30:])
    except: return ""

def ai_diagnose_and_fix():
    """用 LLM 分析错误日志并生成修复"""
    if not LLM_KEY:
        log("⚠️ 无 LLM Key，使用备份恢复")
        return False

    error_log = get_error_log()
    if not error_log.strip():
        error_log = pm2_logs(CORE_NAME)

    try:
        with open(CORE_FILE) as f:
            current_code = f.read()[:3000]
    except:
        current_code = "FILE NOT FOUND"

    prompt = f"""You are an AI guardian fixing a crashed Python process.

ERROR LOG:
{error_log[:2000]}

CURRENT CODE (first 3000 chars):
{current_code[:2000]}

If there's a SyntaxError or ImportError, output the EXACT fix as a Python sed command.
Format: sed -i 's/OLD_TEXT/NEW_TEXT/' {CORE_FILE}

If you can't fix it, reply: RESTORE_BACKUP

Reply with ONLY the fix command or RESTORE_BACKUP."""

    try:
        from openai import OpenAI
        client = OpenAI(api_key=LLM_KEY, base_url=LLM_URL)
        resp = client.chat.completions.create(
            reasoning_effort="max",
            model=LLM_MODEL,
            messages=[{"role":"user","content":prompt}],
            max_tokens=500, temperature=0.1
        )
        fix = resp.choices[0].message.content.strip()
        log(f"🧠 LLM诊断: {fix[:200]}")

        if "RESTORE_BACKUP" in fix:
            return False

        if fix.startswith("sed "):
            subprocess.run(fix, shell=True, timeout=10)
            log("✅ LLM修复已应用")
            return True
        return False
    except Exception as e:
        log(f"⚠️ LLM诊断失败: {e}")
        return False

def blind_recover():
    """盲恢复：从备份恢复文件"""
    log("🔧 从备份恢复")
    subprocess.run(["cp", BACKUP_FILE, CORE_FILE], capture_output=True)

def heal_core():
    """治愈 Core：AI诊断 → 盲恢复 → 重启"""
    status, restarts = pm2_status(CORE_NAME)
    if status == "online":
        return  # 没事

    log(f"❌ Core {status}, restarts={restarts}")

    if restarts >= 3:
        # 三次重启失败 → AI 诊断
        if not ai_diagnose_and_fix():
            blind_recover()
    else:
        pm2_restart(CORE_NAME)
        time.sleep(3)
        s, _ = pm2_status(CORE_NAME)
        if s != "online":
            blind_recover()

    pm2_restart(CORE_NAME)
    log("✅ Core 已治愈")

def check_partner():
    if not r: return
    t = r.get(PARTNER_KEY)
    if not t or time.time() - float(t) > 30:
        log(f"⚠️ Guardian-{PARTNER_ID} 失联，重启")
        pm2_restart(f"yaxiio-guardian-{PARTNER_ID}")

def send_heartbeat():
    if r: r.setex(HEARTBEAT_KEY, 60, str(time.time()))

def run():
    log(f"🛡️ 启动 AI守护模式，搭档 Guardian-{PARTNER_ID}")
    signal.signal(signal.SIGTERM, lambda *_: (log("退出"), exit(0)))
    send_heartbeat()

    while True:
        time.sleep(10)
        send_heartbeat()
        heal_core()
        check_partner()

if __name__ == "__main__":
    run()
