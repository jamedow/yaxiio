#!/usr/bin/env python3
"""Yaxiio 部署钩子: 同步→验证→清缓存→验收"""
import subprocess, sys, time

HK = "root@47.79.20.2"
PASS = "Zhangliang@520"

def ssh(cmd):
    return subprocess.run(
        ["sshpass","-p",PASS,"ssh","-o","StrictHostKeyChecking=no",HK,cmd],
        capture_output=True, text=True, timeout=30
    )

def verify(url, keyword):
    r = subprocess.run(["curl","-sL","-m","10",url], capture_output=True, text=True, timeout=15)
    return keyword in r.stdout

mode = sys.argv[1] if len(sys.argv) > 1 else "sync"
industry = sys.argv[2] if len(sys.argv) > 2 else "power"

if mode in ("sync", "full"):
    print(f"[Hook] 1/5 同步 {industry} → HK Redis...")
    subprocess.run(["python3","/opt/commander/tools/content_sync.py","industry",industry])

print("[Hook] 2/5 清理 SSH...")
subprocess.run("pkill -f 'ssh.*47.79.20.2'", shell=True)

print("[Hook] 3/5 重启 Nuxt 清 ISR...")
ssh("docker restart nuxt-app")

print("[Hook] 4/5 等待就绪...")
time.sleep(10)

print("[Hook] 5/5 验收...")
checks = [
    ("https://www.lightingmetal.com/zh/industries/power/solar-farm", "地面光伏"),
    ("https://www.lightingmetal.com/zh/industries/power/solar-farm/solar-farm-foundation-structure", "基础与支架"),
]
ok = 0
for url, kw in checks:
    if verify(url, kw):
        print(f"  ✅ {kw}")
        ok += 1
    else:
        print(f"  ❌ {kw}")

print("[Hook] 清理 SSH...")
subprocess.run("pkill -f 'ssh.*47.79.20.2'", shell=True)
print(f"[Hook] DONE ({ok}/{len(checks)} passed)")
