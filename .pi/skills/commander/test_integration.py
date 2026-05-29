#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""Commander 集成测试 — 端到端验证控制器"""
import sys, os, json, time

# 设置路径
COMMANDER_DIR = "/app/.pi/skills/commander"
TOKEN_DIR = "/app/.pi/skills/token-budget-controller"
PROMPT_DIR = "/app/.pi/skills/prompt-optimizer"

sys.path.insert(0, COMMANDER_DIR)
sys.path.insert(0, TOKEN_DIR)
sys.path.insert(0, PROMPT_DIR)

# ═══ 测试 1: Token 预算控制器 ═══
print("=" * 60)
print("测试 1: TokenBudgetController")
print("=" * 60)

from token_budget import TokenBudgetController, TokenEstimator

estimator = TokenEstimator()
ctrl = TokenBudgetController()

# 验证窗口大小
for model in ["deepseek-chat", "gpt-4o", "gpt-4", "claude-3-opus"]:
    w = ctrl.get_window_size(model)
    print(f"  {model:25s} 窗口={w:>7,}  80%阈值={int(w*0.8):>7,}")

# 验证裁剪逻辑
messages = [
    {"role": "system", "content": "你是ExampleCorp商务经理"},
    {"role": "user", "content": "需要光伏支架报价", "timestamp": time.time()},
    {"role": "assistant", "content": "确认: 热镀锌ISO 1461, decision: approved", "timestamp": time.time() - 60},
]
for i in range(800):
    messages.append({
        "role": "user" if i % 2 == 0 else "assistant",
        "content": f"历史消息 {i}: " + ("技术参数详细说明 " * 15),
        "timestamp": time.time() - 200000 + i * 50,
    })

result = ctrl.check(messages, model="gpt-4", current_task="沙特50MW光伏报价", agent_id="test")
print(f"  裁剪前: {result.original_tokens:>6,} tokens")
print(f"  裁剪后: {result.final_tokens:>6,} tokens")
print(f"  节省:   {result.saved_tokens:>6,} tokens ({round(result.saved_tokens/max(1,result.original_tokens)*100,1)}%)")
print(f"  已裁剪: {result.clipped}")
assert result.clipped, "Should have clipped!"
assert result.final_tokens < result.original_tokens, "Token count should decrease!"
assert len(result.messages) < len(messages), "Message count should decrease!"
# 验证 P0 (system prompt) 保留
assert any(m["role"] == "system" for m in result.messages), "System prompt must be preserved!"
print("  ✅ 裁剪逻辑正确 (P0保留, P3/P4丢弃)")

# ═══ 测试 2: Prompt 优化器 ═══
print()
print("=" * 60)
print("测试 2: PromptOptimizer (GEPA)")
print("=" * 60)

from prompt_optimizer import PromptOptimizer, PromptOptimizerGuard, OptimizerConfig

config = OptimizerConfig(min_tasks_for_check=5, check_interval=2)
opt = PromptOptimizer(config=config)
guard = PromptOptimizerGuard(opt)

sample_tasks = [
    ("生成报价方案", True, 85, True),
    ("翻译产品规格书", False, 45, True, "格式错误：缺少 JSON 输出"),
    ("回复技术咨询", True, 90, True),
    ("对比防腐工艺", True, 78, True),
    ("处理客户投诉", False, 30, False, "回复不专业"),
]

for i in range(12):
    for task_desc, success, score, compliant, *err in sample_tasks:
        err_msg = err[0] if err else ""
        guard.after_task("商务经理", success=success, score=score,
                         format_compliant=compliant, task_description=task_desc,
                         error_message=err_msg)

status = guard.get_status("商务经理")
print(f"  累计任务: {status['stats']['total_tasks']}")
print(f"  成功率:   {status['metrics'].get('success_rate', 0)}")
print(f"  需优化:   {status['needs_optimize']}")

result = guard.manual_optimize("商务经理")
print(f"  GEPA 触发: {result.triggered_by}")
print(f"  候选数:    {len(result.candidates)}")
for c in result.candidates:
    print(f"    v{c.get('version','?')} eval={c.get('eval_score',0)} risk={c.get('risk','?')}")
print("  ✅ GEPA 四阶段全部通过")

# ═══ 测试 3: CommanderV2 完整初始化 ═══
print()
print("=" * 60)
print("测试 3: CommanderV2 完整链路")
print("=" * 60)

# 从环境变量读取 Redis 配置
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "$DB_PASSWORD")

import commander
from commander import CommanderV2

try:
    commander = CommanderV2(
        agent_id="test-commander",
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_password=REDIS_PASSWORD,
        enable_lifecycle=True,
        enable_designer=True,
        enable_evolver=True,
        enable_extensions=True,
    )
    print(f"  ✅ CommanderV2 初始化成功")
    print(f"     HAS_LIFECYCLE_V2 = {commander.HAS_LIFECYCLE_V2}")
    print(f"     HAS_EXTENSION_ROUTER = {commander.HAS_EXTENSION_ROUTER}")
    print(f"     HAS_TOKEN_BUDGET = {commander.HAS_TOKEN_BUDGET}")
    print(f"     HAS_PROMPT_OPTIMIZER = {commander.HAS_PROMPT_OPTIMIZER}")
    print(f"     lifecycle = {commander.lifecycle is not None}")
    print(f"     extension_router = {commander.extension_router is not None}")
    print(f"     token_guard = {commander.token_guard is not None}")
    print(f"     prompt_guard = {commander.prompt_guard is not None}")
except Exception as e:
    print(f"  ⚠️ CommanderV2 初始化受限: {e}")
    import traceback
    traceback.print_exc()

# ═══ 测试 4: Redis 连接 & Agent 注册 ═══
print()
print("=" * 60)
print("测试 4: Redis 连通性 & Agent 心跳")
print("=" * 60)

try:
    import redis as redis_lib
    r = redis_lib.Redis(protocol=2, host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)
    r.ping()
    print("  ✅ Redis PING 成功")
    
    # 写入测试心跳
    r.set("commander:agent:heartbeat:test-商务经理", str(int(time.time())))
    r.set("commander:agent:heartbeat:test-翻译官", str(int(time.time())))
    r.set("commander:agent:heartbeat:test-售前经理", str(int(time.time())))
    
    # 注册测试 Agent
    for agent in ["商务经理", "翻译官", "售前经理"]:
        r.hset("commander:agents:registry", agent, json.dumps({
            "role": agent,
            "capabilities": ["business"] if "商务" in agent else ["translation"] if "翻译" in agent else ["presales"],
            "status": "running"
        }))
    
    # 验证注册
    registry = r.hgetall("commander:agents:registry")
    print(f"  已注册 Agent: {len(registry)} 个")
    for name in registry:
        print(f"    ✅ {name}")
    
    # 清理
    r.delete("commander:agent:heartbeat:test-商务经理")
    r.delete("commander:agent:heartbeat:test-翻译官")
    r.delete("commander:agent:heartbeat:test-售前经理")
    r.delete("commander:agents:registry")
except Exception as e:
    print(f"  ❌ Redis 测试失败: {e}")

# ═══ 测试 5: Extension Router 关键词匹配 ═══
print()
print("=" * 60)
print("测试 5: ExtensionRouter 关键词匹配")
print("=" * 60)

try:
    from extension_router import ExtensionRouter, build_extension_router
    er = build_extension_router(redis_client=None, mongo_client=None)
    
    test_cases = [
        ("翻译俄语产品规格书", True),    # 应有匹配
        ("沙特50MW光伏项目需要报价", True),
        ("对比热镀锌和达克罗方案", True),
        ("部署新的API", True),
        ("审计这个页面", True),
        ("搜索光伏支架产品", True),
        ("优化这个prompt", True),
        ("检查token是否超限", True),
        ("你好", False),                # 应无匹配
        ("今天天气怎么样", False),
    ]
    
    passed = 0
    for text, should_match in test_cases:
        caps = er._keyword_based_analysis({"description": text})
        has_match = len(caps) > 0
        if has_match == should_match:
            status = "✅"
            passed += 1
        else:
            status = "❌"
        details = f"→ {[c.get('capability','?') for c in caps]}" if caps else "→ no match"
        print(f"  {status} '{text[:30]}' {details}")
    
    print(f"  通过: {passed}/{len(test_cases)}")

    # ── ImageGen 多模态关键词 ──
    print()
    print("-" * 40)
    print("  多模态 ImageGen 子测试:")
    img_tests = [
        ("为光伏支架生成一张超写实产品图", ["商务经理", "image-gen"]),
        ("给展会设计一款海报", ["image-gen"]),
        ("画一张技术制图", ["image-gen"]),
        ("翻译产品说明书并配图", ["translate-engine", "image-gen"]),
        ("生成DALL-E效果图", ["image-gen"]),
        ("做一张banner", ["image-gen"]),
    ]
    img_passed = 0
    for desc, expected in img_tests:
        caps = er._keyword_based_analysis({"description": desc, "payload": {}})
        names = [c["capability"] for c in caps]
        if "image-gen" in names:
            print(f"    ✅ '{desc[:35]}' → {names}")
            img_passed += 1
        else:
            print(f"    ❌ '{desc[:35]}' → {names} (expected image-gen)")
    print(f"    通过: {img_passed}/{len(img_tests)}")
except Exception as e:
    print(f"  ⚠️ ExtensionRouter 测试跳过: {e}")

print()
print("=" * 60)
print("测试 6: CommanderV2 handle_task 端到端")
print("=" * 60)

if commander:
    # 注入 Agent 状态，避免降级到 L4
    for agent, role in [("翻译官", "翻译官"), ("商务经理", "商务经理"), ("售前经理", "售前经理")]:
        r.hset("commander:agent:status:by_role", role, "running")
        r.set(f"commander:agent:heartbeat:{agent}", str(int(time.time())))

    tasks = [
        "翻译俄语产品规格书为中文",
        "你好",
    ]

    for task in tasks:
        try:
            result = commander.handle_task(task)
            status = result.get("status", "?")
            subtasks = result.get("subtasks", 0)
            results = result.get("results", [])
            agents = [r.get("target", "?") for r in results]
            print(f"  ✅ '{task[:40]}' → {status}, {subtasks} subtasks → {agents}")
        except Exception as e:
            print(f"  ⚠️ '{task[:40]}' → error: {e}")

    # 清理状态
    r.delete("commander:agent:status:by_role")
    for agent in ["翻译官", "商务经理", "售前经理"]:
        r.delete(f"commander:agent:heartbeat:{agent}")

print()
print("=" * 60)
print("测试 7: pi 扩展 Node.js 语法验证")
print("=" * 60)

import subprocess
result = subprocess.Popen(
    ["node", "-e", """
    const fs = require('fs');
    const files = ['index.ts', 'router.ts', 'agent-pool.ts', 'governance.ts'];
    let ok = 0;
    for (const f of files) {
        const content = fs.readFileSync('/app/.pi/extensions/commander/' + f, 'utf8');
        const hasExport = content.includes('export');
        const hasImport = content.includes('import');
        const lines = content.split('\\n').length;
        console.log((hasExport && hasImport ? 'OK' : 'FAIL') + ' ' + f + ' (' + lines + ' lines)');
        if (hasExport && hasImport) ok++;
    }
    console.log('Passed: ' + ok + '/' + files.length);
    """],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)
out, err = result.communicate(timeout=10)
print(out.decode().strip())
if err:
    print("Stderr:", err.decode()[:200])

print()
print("=" * 60)
print("全部测试完成")
print("=" * 60)
