"""会话凭据仓（G72 终稿，2026-10-23）——token 唯一的落盘地址。

设计：token 是**会话与网关之间的事**，不是脚本的配置。本文件是会话机器
的内部件（0600），脚本/用户文档都不该直接碰它；``genie login`` 与 Studio
登录闸（经会话咽喉 ``gateway_forward`` 截获）是仅有的两个写入者。

- 路径 ``~/.genie/credentials.json``（2026-10-23 由 config.json 迁入：
  首次读到旧文件即收养并删除）；
- ``gateway`` 地址也住这里（``genie connect <url>`` 写入；env 覆盖；
  公开域名定案后 ``DEFAULT_GATEWAY`` 兜底——内网 IP 不进公开面红线）；
- 凭据解析优先级：显式参数 > env（GENIE_URL / GENIE_API_KEY /
  GENIE_USERNAME）> 本文件 > DEFAULT_GATEWAY。

分层：net（IO 层）。密码**从不**经过本模块——只有 token。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: 缺省网关。None = 未定案：无参 connect 会得到"下一步动作"式报错而非猜测。
DEFAULT_GATEWAY: str | None = None

_OLD_NAME = "config.json"
_NEW_NAME = "credentials.json"


def _store_dir() -> Path:
    """凭据目录（``~/.genie``；测试钉这里换家）。"""
    return Path.home() / ".genie"


def config_path() -> Path:
    """凭据文件路径（``~/.genie/credentials.json``；旧名 config.json 自动迁）。"""
    d = _store_dir()
    new = d / _NEW_NAME
    if not new.exists():
        old = d / _OLD_NAME
        if old.exists():
            try:
                new.write_text(old.read_text(encoding="utf-8"),
                               encoding="utf-8")
                try:
                    new.chmod(0o600)
                except OSError:
                    pass
                old.unlink()
            except OSError:       # 迁移失败 = 继续用旧文件，别拦登录
                return old
    return new


def load_config() -> dict:
    """读凭据；缺失/损坏一律返回 {}（坏文件不当凭据用，由 resolve 指路）。"""
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def save_config(*, gateway: str | None = None, api_key: str | None = None,
                username: str | None = None) -> Path:
    """合并写入（None 字段不动现有值），0600。返回文件路径。"""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = {**load_config(),
           **{k: v for k, v in
              (("gateway", gateway), ("api_key", api_key),
               ("username", username)) if v is not None}}
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:      # Windows FAT 等无 POSIX 权限的文件系统：尽力而为
        pass
    return p


def clear_config(*keys: str) -> None:
    """删掉指定字段（``genie logout`` 清 token 用）。"""
    cfg = load_config()
    if not any(k in cfg for k in keys):
        return
    p = config_path()
    for k in keys:
        cfg.pop(k, None)
    if cfg:
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    else:
        p.unlink(missing_ok=True)


def gateway_source() -> tuple[str | None, str | None]:
    """(网关地址, 来源标签) —— resolve_gateway 的非抛版，同一条链、
    同一归一（strip + 去尾斜杠）。来源标签 ∈ env / config / default。

    报地址给人/agent 看的面（mcp login_status、CLI connect 展示）一律走
    这里，别手抄链——手抄份不归一时，报出去的地址会和实际拨号地址字形
    不同（G101 清账 2026-10-27 前的实测病灶）。
    """
    for cand, src in ((os.environ.get("GENIE_URL") or None, "env"),
                      (load_config().get("gateway") or None, "config"),
                      (DEFAULT_GATEWAY, "default")):
        if cand and str(cand).strip():
            return str(cand).strip().rstrip("/"), src
    return None, None


def resolve_gateway(explicit: str | None = None) -> str:
    """按优先级链解析网关地址；解析不出 → 报错并给**下一步动作**。

    顺序：显式参数 > env ``GENIE_URL`` > credentials.json ``gateway``
    > ``DEFAULT_GATEWAY``。链与归一化的唯一实现 = gateway_source()。
    """
    from ..models.errors import GenieConfigError
    if explicit and str(explicit).strip():
        return str(explicit).strip().rstrip("/")
    gw, _src = gateway_source()
    if gw:
        return gw
    raise GenieConfigError(
        "未配置网关地址。三选一：\n"
        "  ① genie connect <服务地址>      写入本机配置（推荐，一次即可）\n"
        "  ② set GENIE_URL=<服务地址>      环境变量\n"
        "  ③ genie.connect(<服务地址>)     脚本里显式传参")


def resolve_credential(explicit: str | None, env_name: str,
                       config_key: str) -> str | None:
    """凭据解析：显式参数 > env > 凭据仓。缺省返回 None（不猜）。"""
    return (explicit
            or os.environ.get(env_name) or None
            or load_config().get(config_key) or None)
