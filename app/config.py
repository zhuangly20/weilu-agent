"""配置加载：YAML 剧本配置 + 环境变量模型供应商配置。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PROJECT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """轻量 .env 加载：只填充尚未设置的环境变量。"""
    env_file = PROJECT_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


@dataclass
class ProviderConfig:
    ident: str
    base_url: str
    api_key: str
    model: str
    timeout_connect: float = 5.0
    timeout_read: float = 60.0


@dataclass
class Settings:
    api_key: str  # 本服务对清小搭网关的鉴权密钥
    providers: list[ProviderConfig] = field(default_factory=list)
    public_base_url: str = "http://127.0.0.1:8200"  # 构建附件 fileUrl 用


def _load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_theme_config() -> dict:
    return _load_yaml("themes.yaml")


def load_characters() -> dict:
    hist = _load_yaml("characters.yaml").get("historical", [])
    peers = _load_yaml("peers.yaml").get("peers", [])
    return {c["id"]: c for c in [*hist, *peers]}


def load_crisis_config() -> dict:
    return _load_yaml("crisis_keywords.yaml")


def load_group_v2_config() -> dict:
    return _load_yaml("group_v2.yaml")


_GROUP_THEME_FILES = {
    "academic": "group_v2.yaml",
    "connection": "group_connection.yaml",
    "love": "group_love.yaml",
    "career": "group_career.yaml",
}


def load_group_theme_config(theme: str) -> dict:
    """按主题加载四活动团体配置；未知主题回落到减压（academic）。"""
    name = _GROUP_THEME_FILES.get(theme or "academic", "group_v2.yaml")
    return _load_yaml(name)


# 史记人物库：与史料人物无关的通用/现代条目不进入时空对话
_SHIJI_EXCLUDED = {"通用角色", "局座", "毛选专家", "权谋军师（知乎权谋蒸馏）", "掌印太监"}
_shiji_cache: list[dict] | None = None


def load_shiji_figures() -> list[dict]:
    global _shiji_cache
    if _shiji_cache is None:
        raw = json.loads((CONFIG_DIR / "shiji_registry.json").read_text(encoding="utf-8"))
        _shiji_cache = [p for p in raw if p.get("name") not in _SHIJI_EXCLUDED]
    return _shiji_cache


def _load_providers_from_env() -> list[ProviderConfig]:
    """史记风格的 AI_PROVIDER_<ID>_* 变量；退回到单组 AI_BASE_URL/AI_API_KEY/AI_MODEL。"""
    providers: list[ProviderConfig] = []
    ids = os.environ.get("AI_PROVIDER_IDS", "").strip()
    if ids:
        for ident in [s.strip() for s in ids.split(",") if s.strip()]:
            prefix = f"AI_PROVIDER_{ident.upper()}_"
            base_url = os.environ.get(f"{prefix}BASE_URL", "").strip()
            api_key = os.environ.get(f"{prefix}API_KEY", "").strip()
            model = os.environ.get(f"{prefix}MODEL", "").strip()
            if base_url and api_key and model:
                providers.append(
                    ProviderConfig(ident=ident, base_url=base_url, api_key=api_key, model=model)
                )
    if not providers:
        base_url = os.environ.get("AI_BASE_URL", "").strip()
        api_key = os.environ.get("AI_API_KEY", "").strip()
        model = os.environ.get("AI_MODEL", "").strip()
        if base_url and api_key and model:
            providers.append(
                ProviderConfig(ident="default", base_url=base_url, api_key=api_key, model=model)
            )
    return providers


def load_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("WEILU_API_KEY", "sk-weilu-dev-key"),
        providers=_load_providers_from_env(),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8200").rstrip("/"),
    )
