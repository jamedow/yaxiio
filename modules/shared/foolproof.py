"""
Yaxiio Fool-Proof Foundation — 公共防呆工具层
===============================================
每个模块通过调用这些工具获得防呆能力，无需各自实现。

设计参考:
  - Windows: 合理的默认值 + 危险的确认弹窗
  - VSCode: GUI 预设 → settings.json 精确控制
  - Docker: 默认安全配置 + 可选的 --privileged
  - Git: 普通操作不确认 / --force 需要显式声明
"""

# ═══════════════════════════════════════════════
# 一、语义预设映射
# ═══════════════════════════════════════════════

QUALITY_PRESETS = {
    "fast": {
        "model": "deepseek-flash",
        "thinking": "off",
        "max_retries": 1,
        "temperature": 0.5,
        "label": "快速",
        "description": "适合翻译、分类、简单查询等高频轻量任务",
    },
    "standard": {
        "model": "deepseek-chat",
        "thinking": "medium",
        "max_retries": 3,
        "temperature": 0.3,
        "label": "标准",
        "description": "适合大多数业务场景，平衡速度与质量",
    },
    "premium": {
        "model": "deepseek-max",
        "thinking": "high",
        "max_retries": 5,
        "temperature": 0.1,
        "label": "高质量",
        "description": "适合审计、分析、复杂推理等对准确性要求极高的场景",
    },
}

def apply_quality_preset(quality: str, overrides: dict = None) -> dict:
    """
    将语义化的 quality 字段展开为具体参数。
    
    用法:
        config = apply_quality_preset("standard")
        # → {"model": "deepseek-chat", "thinking": "medium", ...}
        
        config = apply_quality_preset("standard", {"max_retries": 5})
        # → 覆盖个别参数
    
    防呆点: 普通用户只需要知道 "快速/标准/高质量"，不需要知道 model/temperature。
    """
    if quality not in QUALITY_PRESETS:
        available = ", ".join(QUALITY_PRESETS.keys())
        raise ValueError(
            f"不支持的质量等级 '{quality}'。\n"
            f"可用选项: {available}\n"
            f"提示: 使用 'standard' 作为默认值。"
        )
    
    config = dict(QUALITY_PRESETS[quality])
    if overrides:
        for k, v in overrides.items():
            if k in config:
                config[k] = v
    return config


# ═══════════════════════════════════════════════
# 二、安全默认值
# ═══════════════════════════════════════════════

DEFAULTS = {
    "task_timeout": 300,         # 秒，防止单任务无限运行
    "max_retries": 3,            # 避免无限重试浪费
    "max_concurrent_agents": 10, # 防止资源耗尽
    "sandbox_enabled": True,     # 代码执行默认隔离
    "audit_enabled": True,       # 审计不可绕过
    "l5_enabled": True,          # 默认参与进化
    "agent_quadrant": "ephemeral", # 默认用完即弃（最安全）
    "output_max_size": 5 * 1024 * 1024,  # 5MB 输出上限
    "subtask_max_count": 50,     # 防止子任务爆炸
    "thinking_default": "medium",
    "temperature_default": 0.3,
}

def safe_default(key: str):
    """获取安全默认值。不存在时返回 None 并打印警告，不崩溃。"""
    val = DEFAULTS.get(key)
    if val is None:
        print(f"[FoolProof] ⚠️ 未知的默认值键: '{key}'，使用了 None", flush=True)
    return val


# ═══════════════════════════════════════════════
# 三、输入校验
# ═══════════════════════════════════════════════

def validate_in_range(value, name: str, min_val, max_val) -> int:
    """校验数值在范围内，超出时自动钳位并警告。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        print(f"[FoolProof] ⚠️ '{name}' 应为数字，收到 '{value}'，使用默认值", flush=True)
        return (min_val + max_val) // 2
    
    if v < min_val:
        print(f"[FoolProof] ⚠️ '{name}'={v} 太小，已钳位到 {min_val}", flush=True)
        return min_val
    if v > max_val:
        print(f"[FoolProof] ⚠️ '{name}'={v} 太大，已钳位到 {max_val}", flush=True)
        return max_val
    return v


def validate_not_empty(value, name: str) -> str:
    """校验字符串非空。"""
    if not value or not str(value).strip():
        print(f"[FoolProof] ⚠️ '{name}' 为空，使用了占位值", flush=True)
        return f"未命名{name}"
    return str(value).strip()


def validate_one_of(value, name: str, allowed: list) -> str:
    """校验值在允许列表中。不在列表时使用第一个允许值。"""
    if value not in allowed:
        print(f"[FoolProof] ⚠️ '{name}'='{value}' 不在允许范围 {allowed}，"
              f"已使用 '{allowed[0]}'", flush=True)
        return allowed[0]
    return value


# ═══════════════════════════════════════════════
# 四、优雅降级
# ═══════════════════════════════════════════════

def try_primary_fallback(primary_fn, fallback_fn, error_label: str):
    """
    尝试主路径，失败时自动降级到备用路径。
    
    用法:
        result = try_primary_fallback(
            lambda: call_llm_judge(task),
            lambda: rule_based_score(task),
            "LLM评分"
        )
    
    防呆点: 用户不需要关心 LLM 是否可用——系统自动降级，任务不中断。
    """
    try:
        return primary_fn()
    except Exception as e:
        print(f"[FoolProof] 🔄 '{error_label}' 失败 ({e})，降级到备用方案", flush=True)
        return fallback_fn()


# ═══════════════════════════════════════════════
# 五、危险操作保护
# ═══════════════════════════════════════════════

RISK_LEVELS = {
    "create_agent":     {"level": "low",    "confirm": False, "undo_window": None},
    "modify_config":    {"level": "medium", "confirm": False, "undo_window": "7d"},
    "delete_skill":     {"level": "high",   "confirm": True,  "undo_window": "30d"},
    "destroy_core":     {"level": "severe", "confirm": True,  "undo_window": "24h",
                         "double_confirm": True, "cooldown_seconds": 10},
    "reset_experience": {"level": "disaster","confirm": True, "undo_window": None,
                         "require_text_confirmation": "I UNDERSTAND"},
}

def assess_risk(action: str, context: dict = None) -> dict:
    """
    评估操作风险等级，返回所需的防呆措施。
    
    用法:
        risk = assess_risk("delete_skill", {"skill_name": "translate-engine"})
        if risk["confirm"]:
            show_confirmation_dialog(risk)
    """
    return RISK_LEVELS.get(action, {"level": "low", "confirm": False})


# ═══════════════════════════════════════════════
# 六、用户友好的错误信息
# ═══════════════════════════════════════════════

def friendly_error(operation: str, detail: str, suggestion: str = "") -> str:
    """
    生成人类可读的错误信息。
    
    不是: "KeyError: 'model'"
    而是: "创建 Agent 失败: 能力卡片缺少 'model' 字段。
           建议: 在卡片中添加 model: 'deepseek-chat' 或使用 quality: 'standard'。"
    """
    msg = f"❌ {operation}失败\n   {detail}"
    if suggestion:
        msg += f"\n   💡 {suggestion}"
    return msg


# ═══════════════════════════════════════════════
# 七、渐进式信息披露标记
# ═══════════════════════════════════════════════

# ═══════════════════════════════════════════════
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
}

def get_visible_fields(card: dict, user_tier: int = 1) -> dict:
    """
    根据用户等级过滤能力卡片字段。
    
    user_level=1 → 只显示 basic 字段 (3-5个)
    user_level=2 → 显示 basic + advanced 字段 (8-12个)
    user_level=3 → 显示全部 (20+)
    
    防呆点: 普通用户不会被 20 个参数吓到。
    """
    visible = {}
    meta = card.get("_ui_meta", {})
    for key, value in card.items():
        if key.startswith("_"):
            continue
        field_level = meta.get(key, {}).get("ui_level", UI_LEVEL["developer"])
        if field_level <= user_tier:
            visible[key] = value
    return visible


# ═══════════════════════════════════════════════
# 八、能力卡片加载时的防呆
# ═══════════════════════════════════════════════

def validate_card(card: dict) -> list:
    """
    校验能力卡片的完整性和正确性。返回问题列表。
    空列表 = 卡片合法。
    
    防呆点: 加载卡片时自动检测常见配置错误。
    """
    issues = []
    
    # 必需字段检查
    required = ["name", "role", "model"]
    for field in required:
        if field not in card:
            issues.append(f"缺少必需字段 '{field}'")
    
    # quality 与裸参数冲突检查
    if "quality" in card:
        preset = QUALITY_PRESETS.get(card["quality"])
        if preset:
            for key in preset:
                if key in card and key != "quality":
                    issues.append(
                        f"同时设置了 quality='{card['quality']}' 和 {key}='{card[key]}'。"
                        f"quality 预设已经包含了 {key} 的值。如果要自定义，请移除 quality 字段。"
                    )
    
    # 数值范围检查
    if "max_retries" in card:
        card["max_retries"] = validate_in_range(card["max_retries"], "max_retries", 1, 10)
    if "task_timeout" in card:
        card["task_timeout"] = validate_in_range(card["task_timeout"], "task_timeout", 30, 3600)
    
    # 四象限检查
    valid_quadrants = ["core", "strategic", "utility", "ephemeral"]
    if "quadrant" in card:
        card["quadrant"] = validate_one_of(card["quadrant"], "quadrant", valid_quadrants)
    
    return issues


# ═══════════════════════════════════════════════
# 九、Agent 生命周期的防呆
# ═══════════════════════════════════════════════

def can_destroy_agent(agent_name: str, quadrant: str, 
                      active_tasks: int = 0, 
                      dependents: list = None) -> tuple:
    """
    检查是否可以安全销毁一个 Agent。
    
    返回: (allowed: bool, reason: str)
    
    防呆点: Core Agent 不可销毁，有活跃任务的 Agent 需确认。
    """
    if quadrant == "core":
        return False, (
            f"'{agent_name}' 是核心 Agent (quadrant=core)，不可销毁。\n"
            f"Core Agent 是系统基础服务，销毁会影响所有依赖它的任务。\n"
            f"如需更新，请使用 '重建' 操作。"
        )
    
    if active_tasks > 0:
        return False, (
            f"'{agent_name}' 正在执行 {active_tasks} 个任务，不可销毁。\n"
            f"请等待任务完成或手动取消任务后再试。"
        )
    
    if dependents and len(dependents) > 0:
        dep_list = ", ".join(dependents[:5])
        return False, (
            f"'{agent_name}' 被 {len(dependents)} 个 Agent/服务依赖: {dep_list}。\n"
            f"销毁它会导致这些依赖方出错。请先解除依赖关系。"
        )
    
    if quadrant == "strategic":
        return True, (
            f"'{agent_name}' 将在闲置超时后自动休眠。\n"
            f"如确认销毁，旧版本将保留 24 小时可恢复。"
        )
    
    return True, ""
