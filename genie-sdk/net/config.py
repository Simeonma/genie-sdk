"""凭据仓（G72 终稿，2026-10-23）——token 唯一的落盘地址。

设计：token 是 **worker 与网关之间的事**，不是脚本的配置。本文件是本机
的内部件（0600），脚本/用户文档都不该直接碰它；``genie login`` 与 Studio
登录闸（经 worker 的 ``gateway_forward`` 截获）是仅有的两个写入者。

- 路径 ``~/.genie/credentials.json``；
- ``gateway`` 地址也住这里（``genie connect <url>`` 写入；env 覆盖；
  公开域名定案后 ``DEFAULT_GATEWAY`` 兜底——内网 IP 不进公开面红线）；
- 凭据解析优先级：env（GENIE_URL / GENIE_API_KEY / GENIE_USERNAME）>
  本文件 > DEFAULT_GATEWAY。

分层：net（IO 层）。密码**从不**经过本模块——只有 token。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: 缺省网关（2026-11-05 对外门开通：genie-optics.com，备案/防火墙/DNS 齐；
#: 注意拼写带 s——genie-optic.com 无 s 从未注册。v1 G72 至此销账）。
DEFAULT_GATEWAY: str | None = "https://genie-optics.com"

_NAME = "credentials.json"


def _store_dir() -> Path:
    """凭据目录（``~/.genie``；测试钉这里换家）。"""
    return Path.home() / ".genie"


def config_path() -> Path:
    """凭据文件路径（``~/.genie/credentials.json``）。"""
    return _store_dir() / _NAME


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
    同一归一（strip + 去尾斜杠）。来源标签 ∈ backend / env / config / default。

    链（高→低）：``GENIE_BACKEND``（部署覆盖）> ``GENIE_URL`` > 凭据仓
    ``gateway`` > ``DEFAULT_GATEWAY``。2026-10-31 收口：``GENIE_BACKEND``
    原先只在 worker 侧单独读（`kernel_session.backend()`，该别名同日删除），
    SDK 侧不认——同一台机器上"SDK 连 A、GUI 连 localhost"。现在两层共用
    这一条链，且都直接调 ``resolve_gateway()``。

    报地址给人的面（CLI 展示）一律走
    这里，别手抄链——手抄份不归一时，报出去的地址会和实际拨号地址字形
    不同（G101 清账 2026-10-27 前的实测病灶）。
    """
    for cand, src in ((os.environ.get("GENIE_BACKEND") or None, "backend"),
                      (os.environ.get("GENIE_URL") or None, "env"),
                      (load_config().get("gateway") or None, "config"),
                      (DEFAULT_GATEWAY, "default")):
        if cand and str(cand).strip():
            return str(cand).strip().rstrip("/"), src
    return None, None


def resolve_gateway(explicit: str | None = None) -> str:
    """按优先级链解析网关地址；解析不出 → 报错并给**下一步动作**。

    顺序：显式参数 > ``GENIE_BACKEND``（部署覆盖）> ``GENIE_URL`` >
    credentials.json ``gateway`` > ``DEFAULT_GATEWAY``。
    链与归一化的唯一实现 = gateway_source()。

    **没有"本机兜底"**（2026-10-31 裁）：曾有一条 worker 专用的
    ``127.0.0.1:9100`` 兜底，那是猜——与 ``DEFAULT_GATEWAY = None``
    的定案（"填一个打不通的地址比不填更糟"）相矛盾，也让同一问题出现
    两种表达。现在两边同一条链、同一种失败：报错并给三条下一步。
    """
    from ..models.errors import GenieConfigError
    if explicit and str(explicit).strip():
        return str(explicit).strip().rstrip("/")
    gw, _src = gateway_source()
    if gw:
        return gw
    raise GenieConfigError(
        "未配置网关地址——先跑一次：\n"
        "  genie connect <服务地址>\n"
        "（写进本机配置，一次即可）")


def resolve_credential(env_name: str, config_key: str) -> str | None:
    """凭据解析：env > 凭据仓。缺省返回 None（不猜）。"""
    return (os.environ.get(env_name) or None
            or load_config().get(config_key) or None)
