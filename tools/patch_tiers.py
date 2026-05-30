#!/usr/bin/env python3
"""Add four-tier user maturity model to foolproof.py"""
path = "/opt/yaxiio/modules/shared/foolproof.py"
with open(path) as f:
    content = f.read()

old = '''# 字段可见性级别
UI_LEVEL = {
    "basic": 1,      # 始终显示（quality, description）
    "advanced": 2,   # 高级模式显示（model, thinking, temperature）
    "developer": 3,  # 开发者模式显示（few_shot_examples, custom_schema）
}'''

new = '''# ═══════════════════════════════════════════════
# 七-2、四层用户等级体系 (Four-Tier User Maturity Model)
# ═══════════════════════════════════════════════

USER_TIERS = {
    1: {"name": "平民级", "label": "Civilian",
        "description": "不需要任何技术背景。描述任务目标，系统自动完成。",
        "ui_level": 1,
        "can": ["提交任务", "查看结果", "选择卡片", "调整 quality"],
        "cannot": ["修改参数", "创建 Agent", "查看日志", "删除数据"],
        "max_concurrent_tasks": 3},
    2: {"name": "维护人员级", "label": "Maintainer",
        "description": "会看使用说明书，能做一些基础调整。",
        "ui_level": 2,
        "can": ["平民级所有权限", "修改常用参数", "查看日志", "重启 Agent", "导出配置"],
        "cannot": ["修改宪法", "销毁 Core Agent", "修改底层 Schema"],
        "max_concurrent_tasks": 10},
    3: {"name": "技术人员级", "label": "Technician",
        "description": "了解 Yaxiio 各项能力的设计思想，能对深度参数做调优。",
        "ui_level": 3,
        "can": ["维护人员级所有权限", "修改所有卡片参数", "创建/销毁 Agent", "调整评分权重", "配置模型路由"],
        "cannot": ["修改宪法白名单", "修改系统核心代码"],
        "max_concurrent_tasks": 50},
    4: {"name": "大神级", "label": "Master",
        "description": "对 Yaxiio 的设计思想和实现方式有深入了解，能进行底层调优甚至重构。",
        "ui_level": 4,
        "can": ["技术人员级所有权限", "修改宪法", "修改系统核心代码", "自定义协议", "联邦部署"],
        "cannot": [],
        "max_concurrent_tasks": 200},
}

def get_tier(tier_id: int) -> dict:
    """获取用户等级定义。无效 ID 返回平民级（安全默认）。"""
    return USER_TIERS.get(tier_id, USER_TIERS[1])

def check_permission(tier_id: int, action: str) -> tuple:
    """检查某等级用户是否可以执行某操作。返回 (allowed, message)。"""
    tier = get_tier(tier_id)
    if action in tier["can"]:
        return True, ""
    if action in tier.get("cannot", []):
        for tid in sorted(USER_TIERS):
            if tid > tier_id and action in USER_TIERS[tid]["can"]:
                hint = f" 升级到「{USER_TIERS[tid]['name']}」即可使用此功能。"
                return False, f"「{tier['name']}」不支持「{action}」。{hint}"
        return False, f"「{tier['name']}」不支持「{action}」。"
    return True, ""

# 字段可见性级别（适配四层体系）
UI_LEVEL = {
    "basic": 1,       # 平民级可见
    "advanced": 2,    # 维护人员级可见
    "developer": 3,   # 技术人员级可见
    "master": 4,      # 大神级可见
}'''

if old in content:
    content = content.replace(old, new)
    # Update get_visible_fields docstring and parameter name
    content = content.replace(
        "def get_visible_fields(card: dict, user_level: int = 1) -> dict:",
        "def get_visible_fields(card: dict, user_tier: int = 1) -> dict:"
    )
    content = content.replace(
        "field_level <= user_level:",
        "field_level <= user_tier:"
    )
    with open(path, "w") as f:
        f.write(content)
    print("OK: four-tier system added")
else:
    print("FAIL: not found")
