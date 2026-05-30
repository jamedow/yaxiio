"""
SkillClassLoader — JVM ClassLoader 风格的分层 Skill 加载器
============================================================
双亲委派模型 + 命名空间隔离 + 热加载

CoreSkillLoader:      Yaxiio 内置 Skill，不可被覆盖（类比 Bootstrap ClassLoader）
IndustrySkillLoader:  行业 Skill，命名空间隔离（类比 Extension ClassLoader）
UserSkillLoader:      用户自定义 Skill（类比 Application ClassLoader）

用法:
    core = CoreSkillLoader(skill_dir="/opt/yaxiio/skills/core")
    medical = IndustrySkillLoader(parent=core, namespace="medical",
                                   skill_dir="/opt/yaxiio/skills/medical")
    user = UserSkillLoader(parent=medical,
                            skill_dir="/opt/yaxiio/skills/user")
    
    skill = user.load_skill("translate-engine")  # 双亲委派查找
"""
import os, json, time
from typing import Dict, Optional


class SkillNotFoundError(Exception):
    """Skill 未找到异常"""
    pass


class SkillClassLoader:
    """基类：分层 Skill 加载器"""

    def __init__(self, parent=None, namespace="", skill_dir=None):
        self.parent = parent          # 父加载器
        self.namespace = namespace    # 命名空间前缀
        self.skill_dir = skill_dir    # Skill 目录
        self._loaded: Dict[str, dict] = {}  # 已加载的 Skill
        self._mtimes: Dict[str, float] = {} # 文件修改时间（热加载用）

    def load_skill(self, name: str) -> dict:
        """
        双亲委派模型: 先问父加载器，没有再自己加载。
        
        类比: JVM ClassLoader.loadClass()
        """
        # 1. 检查是否已加载（缓存）
        if name in self._loaded:
            return self._loaded[name]

        # 2. 委派给父加载器
        if self.parent:
            try:
                return self.parent.load_skill(name)
            except SkillNotFoundError:
                pass  # 父加载器没有，自己加载

        # 3. 自己加载
        skill = self._load_from_disk(name)
        if not skill:
            raise SkillNotFoundError(
                f"Skill '{name}' not found in {self.skill_dir}"
            )

        # 4. 命名空间隔离
        if self.namespace:
            skill = self._apply_namespace(skill)

        self._loaded[name] = skill
        return skill

    def _load_from_disk(self, name: str) -> Optional[dict]:
        """从磁盘加载 Skill"""
        if not self.skill_dir or not os.path.isdir(self.skill_dir):
            return None

        skill_path = os.path.join(self.skill_dir, name, "SKILL.md")
        if not os.path.exists(skill_path):
            return None

        # 热加载检测
        mtime = os.path.getmtime(skill_path)
        if name in self._mtimes and self._mtimes[name] == mtime:
            return self._loaded.get(name)  # 未变化，返回缓存

        self._mtimes[name] = mtime
        with open(skill_path) as f:
            content = f.read()

        return {
            "name": name,
            "content": content,
            "path": skill_path,
            "namespace": self.namespace,
            "loader": self.__class__.__name__,
            "loaded_at": time.time(),
        }

    def _apply_namespace(self, skill: dict) -> dict:
        """给 Skill 内容加上命名空间前缀，避免冲突"""
        if not self.namespace:
            return skill
        # 在 content 中标记命名空间
        skill["namespace"] = self.namespace
        skill["content"] = (
            f"# Namespace: {self.namespace}\n"
            f"# All terms below are scoped to '{self.namespace}'\n\n"
            f"{skill['content']}"
        )
        return skill

    def reload(self, name: str = None):
        """热加载: 清除缓存，下次访问时重新从磁盘加载"""
        if name:
            self._loaded.pop(name, None)
            self._mtimes.pop(name, None)
        else:
            self._loaded.clear()
            self._mtimes.clear()

    def list_loaded(self) -> list:
        """列出本加载器已加载的 Skill"""
        return [
            {"name": k, "namespace": v.get("namespace", ""),
             "loader": v.get("loader", "?")}
            for k, v in self._loaded.items()
        ]


class CoreSkillLoader(SkillClassLoader):
    """
    核心 Skill 加载器 — 不可被覆盖。
    类比: JVM Bootstrap ClassLoader (rt.jar)
    """
    def __init__(self, skill_dir=None):
        super().__init__(
            parent=None,  # 没有父加载器
            namespace="",  # 核心 Skill 不加命名空间
            skill_dir=skill_dir or "/opt/yaxiio/skills/core"
        )


class IndustrySkillLoader(SkillClassLoader):
    """
    行业 Skill 加载器 — 命名空间隔离。
    类比: JVM Extension ClassLoader (lib/ext)
    
    不同行业的同名术语不会混淆:
      medical:blood_pressure ≠ sport:blood_pressure
    """
    def __init__(self, parent, namespace, skill_dir=None):
        super().__init__(
            parent=parent,
            namespace=namespace,
            skill_dir=skill_dir or f"/opt/yaxiio/skills/{namespace}"
        )


class UserSkillLoader(SkillClassLoader):
    """
    用户自定义 Skill 加载器。
    类比: JVM Application ClassLoader (classpath)
    """
    def __init__(self, parent, skill_dir=None):
        super().__init__(
            parent=parent,
            namespace="user",
            skill_dir=skill_dir or "/opt/yaxiio/skills/user"
        )
