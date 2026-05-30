"""
雅西任务侦察框架 (Yaxiio Reconnaissance Framework)
=================================================
接到新任务后，先派遣侦察 Agent 探明任务的体量、范围、复杂度，
Commander 据此决定拆分策略、并发度、超时配置。

设计原则:
  - 零行业硬编码：侦察维度是通用的，不绑定具体业务
  - 可组合：每个侦察维度独立可插拔
  - 渐进式：侦察结果越详细，后续执行越精准

侦察维度:
  1. 体量 (volume)      — 文件/页面/记录数量
  2. 范围 (scope)       — 目录结构、数据分布
  3. 复杂度 (complexity) — 文件类型、嵌套深度、依赖关系
  4. 风险 (risk)         — 大文件、二进制、异常格式

用法:
  recon = TaskReconnaissance(commander)
  report = recon.scout(task_id, payload)
  # report 包含: volume/scope/complexity/risk + 建议的 chunk_size/max_concurrent/timeout
"""

import json, os, time

# ═══════════════════════════════════════════════
# 侦察维度定义 (可扩展)
# ═══════════════════════════════════════════════

RECON_DIMENSIONS = {
    "volume": {
        "name": "体量探测",
        "description": "统计文件/页面/记录的总数量",
        "priority": 1,
    },
    "scope": {
        "name": "范围探测",
        "description": "分析目录结构、数据分布、主要模块",
        "priority": 2,
    },
    "complexity": {
        "name": "复杂度探测",
        "description": "分析文件类型、嵌套深度、依赖关系",
        "priority": 3,
    },
    "risk": {
        "name": "风险探测",
        "description": "识别大文件、二进制、异常格式等潜在问题",
        "priority": 4,
    },
}


class ReconReport:
    """侦察报告 — 所有维度探测结果的汇总"""

    def __init__(self):
        self.volume = {}       # {total_files, total_dirs, total_size_mb, file_types: {...}}
        self.scope = {}        # {top_dirs: [...], depth_max, breadth_max}
        self.complexity = {}   # {avg_file_size, max_file_size, nested_depth, has_binary}
        self.risk = []         # [{file, type, size_mb, reason}, ...]
        self.recommendations = {}  # {chunk_size, max_concurrent, suggested_timeout}
        self.elapsed_ms = 0
        self.errors = []

    def to_dict(self) -> dict:
        return {
            "volume": self.volume,
            "scope": self.scope,
            "complexity": self.complexity,
            "risk": self.risk[:20],
            "recommendations": self.recommendations,
            "elapsed_ms": self.elapsed_ms,
        }


class ReconAgent:
    """侦察 Agent — 执行具体的探测动作，不依赖 LLM，纯文件系统操作"""

    def __init__(self, commander=None):
        self.commander = commander

    def probe_volume(self, target: str) -> dict:
        """探测体量：遍历目录统计文件数、目录数、大小"""
        result = {"total_files": 0, "total_dirs": 0, "total_size_mb": 0, "file_types": {}}
        if not target or not os.path.exists(target):
            return result

        try:
            for root, dirs, files in os.walk(target):
                # 跳过无关目录
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".nuxt",
                             ".output", "__pycache__", ".vite-cache", "dist", "logs")]
                result["total_dirs"] += len(dirs)
                for f in files:
                    result["total_files"] += 1
                    ext = os.path.splitext(f)[1] or "no_ext"
                    result["file_types"][ext] = result["file_types"].get(ext, 0) + 1
                    try:
                        result["total_size_mb"] += os.path.getsize(os.path.join(root, f)) / (1024 * 1024)
                    except OSError:
                        pass
                # 限制遍历深度，避免超大项目超时
                if result["total_files"] > 10000:
                    result["_truncated"] = True
                    break
            result["total_size_mb"] = round(result["total_size_mb"], 2)
        except Exception as e:
            result["_error"] = str(e)[:100]
        return result

    def probe_scope(self, target: str) -> dict:
        """探测范围：分析顶层目录结构"""
        result = {"top_dirs": [], "depth_max": 0, "breadth_max": 0}
        if not target or not os.path.isdir(target):
            return result

        try:
            # 顶层目录
            entries = os.listdir(target)
            dirs = [e for e in entries if os.path.isdir(os.path.join(target, e))
                    and e not in ("node_modules", ".git", ".nuxt", ".output", "__pycache__")]
            result["top_dirs"] = dirs[:30]
            result["breadth_max"] = len(entries)

            # 估算最大深度
            def _max_depth(path, depth=0):
                if depth > 15:
                    return 15
                try:
                    subdirs = [os.path.join(path, d) for d in os.listdir(path)
                               if os.path.isdir(os.path.join(path, d))
                               and d not in ("node_modules", ".git")]
                    if not subdirs:
                        return depth
                    return max(_max_depth(sd, depth + 1) for sd in subdirs[:5])
                except Exception:
                    return depth
            result["depth_max"] = _max_depth(target)
        except Exception as e:
            result["_error"] = str(e)[:100]
        return result

    def probe_complexity(self, target: str, volume: dict = None) -> dict:
        """探测复杂度：大文件、嵌套深度等"""
        result = {"avg_file_size_kb": 0, "max_file_size_kb": 0, "large_files": 0, "has_binary": False}
        if not volume:
            return result
        total = volume.get("total_files", 1) or 1
        result["avg_file_size_kb"] = round(volume.get("total_size_mb", 0) * 1024 / total, 1)
        # 粗略判断文件类型复杂度
        types = volume.get("file_types", {})
        binary_exts = {".png", ".jpg", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".pdf"}
        result["has_binary"] = any(ext in binary_exts for ext in types)
        result["source_files"] = sum(n for ext, n in types.items()
                                     if ext in (".vue", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"))
        return result

    def probe_risk(self, target: str, volume: dict = None) -> list:
        """探测风险：超大文件、异常文件"""
        risks = []
        if not volume:
            return risks
        types = volume.get("file_types", {})
        # 单类型文件过多
        for ext, count in types.items():
            if count > 500:
                risks.append({"type": "high_concentration", "ext": ext, "count": count,
                              "reason": f"单类型文件 {ext} 达到 {count} 个，建议按模块拆分"})
        # 项目过大
        total = volume.get("total_files", 0)
        if total > 5000:
            risks.append({"type": "large_project", "total_files": total,
                          "reason": f"项目共有 {total} 个文件，建议分批执行"})
        if total > 20000:
            risks.append({"type": "very_large_project", "total_files": total,
                          "reason": f"超大项目 ({total} 文件)，建议先做目录级筛选"})
        return risks

    def generate_recommendations(self, report: ReconReport) -> dict:
        """根据侦察结果生成执行建议"""
        vol = report.volume
        total_files = vol.get("total_files", 0)
        total_mb = vol.get("total_size_mb", 0)

        recs = {"chunk_size": 10, "max_concurrent": 3, "suggested_timeout": 120}

        # 根据体量调整
        if total_files > 1000:
            recs["chunk_size"] = 50
            recs["max_concurrent"] = 5
            recs["suggested_timeout"] = 300
        if total_files > 5000:
            recs["chunk_size"] = 100
            recs["max_concurrent"] = 8
            recs["suggested_timeout"] = 600
        if total_mb > 500:
            recs["suggested_timeout"] = max(recs["suggested_timeout"], 600)

        # 根据风险调整
        for risk in report.risk:
            if risk["type"] == "very_large_project":
                recs["chunk_size"] = max(recs["chunk_size"], 200)
                recs["suggested_timeout"] = max(recs["suggested_timeout"], 900)

        return recs


class TaskReconnaissance:
    """任务侦察器 — 编排探测流程，生成侦察报告"""

    # 不同任务类型的默认侦察维度
    ACTION_DIMENSIONS = {
        "site_audit":    ["volume", "complexity", "risk"],      # 审计: 关心文件数和复杂度
        "site_evolve":   ["volume", "complexity", "scope"],     # 进化: 关心结构和复杂度
        "site_drill":    ["volume", "risk"],                     # 沙箱演习: 关心体量和风险
        "site_fix":      ["volume", "risk"],                     # 修复: 定位问题文件
        "site_build":    ["volume", "scope"],                    # 构建: 关心目录结构
        "translate":     ["volume"],                              # 翻译: 只关心文件数
        "i18n":          ["volume", "scope"],                    # 国际化: 关心分布
        "diagnose":      ["volume", "complexity", "risk"],      # 诊断: 全面
        "generate":      ["volume", "scope"],                    # 生成: 关心结构
        "analyze":       ["volume", "complexity", "risk", "scope"],  # 分析: 全面
    }

    # 默认维度 (当 action 未匹配时)
    DEFAULT_DIMENSIONS = ["volume", "scope"]

    def __init__(self, commander=None):
        self.agent = ReconAgent(commander)
        self.commander = commander
        self.dimensions = RECON_DIMENSIONS

    def select_dimensions(self, payload: dict) -> list:
        """根据任务类型选择侦察维度。

        优先级:
          1. payload._recon_dimensions 显式指定
          2. ACTION_DIMENSIONS 按 action 匹配
          3. DEFAULT_DIMENSIONS 兜底
        """
        # 显式指定
        explicit = payload.get("_recon_dimensions")
        if explicit and isinstance(explicit, list):
            valid = [d for d in explicit if d in self.dimensions]
            if valid:
                return valid

        # 按 action 匹配
        action = str(payload.get("action", "")).lower()
        for prefix, dims in self.ACTION_DIMENSIONS.items():
            if action == prefix or action.startswith(prefix):
                return dims

        # 兜底
        return self.DEFAULT_DIMENSIONS

    def scout(self, task_id: str, payload: dict) -> ReconReport:
        """
        执行任务侦察:
          1. 确定侦察目标 (target)
          2. 逐维度探测
          3. 生成建议
          4. 返回报告
        """
        report = ReconReport()
        start = time.time()

        # Commander 根据任务类型选择维度
        selected = self.select_dimensions(payload)

        # 确定侦察目标
        target = payload.get("codebase", payload.get("target_path", ""))
        if not target:
            target = payload.get("target", "")

        print(f"[侦察] {task_id} 开始, target={target}, 维度={selected}", flush=True)

        # 逐维度探测 (按需)
        if "volume" in selected:
            print(f"[侦察] {task_id} 探测体量...", flush=True)
            report.volume = self.agent.probe_volume(target)
            print(f"[侦察] {task_id} 体量: {report.volume.get('total_files',0)} 文件, "
                  f"{report.volume.get('total_size_mb',0)}MB", flush=True)

        if "scope" in selected:
            print(f"[侦察] {task_id} 探测范围...", flush=True)
            report.scope = self.agent.probe_scope(target)

        if "complexity" in selected:
            print(f"[侦察] {task_id} 探测复杂度...", flush=True)
            report.complexity = self.agent.probe_complexity(target, report.volume)

        if "risk" in selected:
            print(f"[侦察] {task_id} 探测风险...", flush=True)
            report.risk = self.agent.probe_risk(target, report.volume)

        # 生成执行建议
        report.recommendations = self.agent.generate_recommendations(report)

        report.elapsed_ms = int((time.time() - start) * 1000)
        print(f"[侦察] {task_id} 完成: {report.volume.get('total_files',0)} 文件, "
              f"建议 chunk={report.recommendations.get('chunk_size')}, "
              f"并发={report.recommendations.get('max_concurrent')}, "
              f"超时={report.recommendations.get('suggested_timeout')}s, "
              f"耗时 {report.elapsed_ms}ms", flush=True)

        # 存储报告到 Redis
        self._save_report(task_id, report)

        return report

    def should_split(self, report: ReconReport) -> bool:
        """判断是否需要拆分任务"""
        total = report.volume.get("total_files", 0)
        return total > report.recommendations.get("chunk_size", 50)

    def _save_report(self, task_id: str, report: ReconReport):
        """存储侦察报告到 Redis, 供 Commander 调度参考"""
        try:
            if self.commander and self.commander.redis:
                key = f"yaxiio:recon:{task_id}"
                self.commander.redis.client.setex(
                    key, 86400,
                    json.dumps(report.to_dict(), ensure_ascii=False, default=str)
                )
        except Exception:
            pass


# 模块级便捷函数
def scout_task(task_id: str, payload: dict, commander=None) -> ReconReport:
    """快捷侦察入口"""
    recon = TaskReconnaissance(commander)
    return recon.scout(task_id, payload)
