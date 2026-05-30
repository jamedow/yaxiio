#!/usr/bin/env python3
"""Refactor: permission tiers → progressive complexity disclosure"""
path = "/opt/yaxiio/modules/shared/foolproof.py"
with open(path) as f:
    content = f.read()

# Find and replace the entire USER_TIERS section
old_marker = "# 七-2、四层用户等级体系"
idx = content.find(old_marker)
end_marker = "UI_LEVEL = {"
idx2 = content.find(end_marker, idx)
old_block = content[idx:idx2]

new_block = '''# 七-2、四层复杂度暴露等级 (Progressive Disclosure Tiers)
# ==========================================================
# 这不是权限控制（RBAC），而是渐进式信息披露。
# 同一份能力卡片，不同等级看到不同数量的字段。
# 所有人都能做所有操作——区别只是系统替你填了多少参数。
#
# 设计参考:
#   相机: 自动模式(tier1) vs 光圈优先(tier2) vs 全手动(tier4)
#   VSCode: 设置面板(tier1) vs settings.json(tier4)
#   macOS: 系统偏好(tier1) vs defaults命令(tier4)
#
# 核心理念: 不是"你不能调"，而是"你不用调，我已经调好了"。

COMPLEXITY_TIERS = {
    1: {"name": "平民级",
        "description": "只需要描述任务目标。系统自动搞定一切。",
        "visible_field_level": 1,
        "auto_fill": True},
    2: {"name": "维护人员级",
        "description": "会看说明书。能调常用的几个参数。",
        "visible_field_level": 2,
        "auto_fill": True},
    3: {"name": "技术人员级",
        "description": "了解设计思想。能精确控制每个参数。",
        "visible_field_level": 3,
        "auto_fill": False},
    4: {"name": "大神级",
        "description": "深入理解实现。能改底层 Schema。",
        "visible_field_level": 99,
        "auto_fill": False},
}

def get_complexity_tier(tier_id: int) -> dict:
    """获取复杂度等级。无效ID默认平民级——宁可少显示，也不吓到用户。"""
    return COMPLEXITY_TIERS.get(tier_id, COMPLEXITY_TIERS[1])

# 字段可见性等级（标注能力卡片中每个字段的展示层级）
FIELD_LEVEL = {
    "basic": 1,       # 平民级可见: quality, description
    "common": 2,      # 维护级可见: model, thinking, temperature
    "advanced": 3,    # 技术级可见: few_shot, custom_schema
    "internal": 4,    # 大神级可见: _ui_meta, protocol
}

'''

content = content.replace(old_block, new_block)

# Update get_visible_fields to use new naming
content = content.replace(
    "def get_visible_fields(card: dict, user_tier: int = 1) -> dict:",
    "def get_visible_fields(card: dict, complexity_tier: int = 1) -> dict:"
)
content = content.replace("field_level <= user_tier:", 
    "tier_info = get_complexity_tier(complexity_tier)\n            field_level <= tier_info.get(\"visible_field_level\", 1):")

# Update docstring  
od = "    根据用户等级过滤能力卡片字段。"
nd = "    根据复杂度等级渐进式披露字段（不是权限控制）。"
content = content.replace(od, nd)

od2 = "    平民级(tier=1) → 只显示 basic 字段 (3-5个)"
nd2 = "    平民级 → 4个字段 (quality, description)"
content = content.replace(od2, nd2)

with open(path, "w") as f:
    f.write(content)
print("✅ Refactored")
