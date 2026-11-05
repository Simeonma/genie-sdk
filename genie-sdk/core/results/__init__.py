"""genie.core.results — 结果读取（h5→对象）唯一口径。

契约 → ``MODMAP.md``（现行 · 站长审毕 2026-11-05；裁决 DECISIONS Q4.5）：

- **SDK 零计算、零物理**：只还原始 fields / 轴 / shape；派生量（透过率 /
  峰频）用户自己 numpy，产品化派生量走「内核算好写进 h5」（Q4.5）。
- **零 HTTP、零状态机**：结果一律本地 ``.gensim`` 成员（Q2.2-1）；唯一
  「守卫」= 成员在不在——worker 侧一句话明说，本包不谈 state（Q2.2-2A）。
- **零缓存**：读数即解析，持有归调用方（图禁令 4）。
- **digest 只读 attrs**：缺键 = None，等内核 b2 写侧；无 HTTP 回退（net
  五方法无此口子）。
- v1 只参考不照抄（core 图禁令 1）；v1 教训必带（图 §1）：G144 bytes
  域标 decode / G95 manifest shape 正源 / R25 field 不当轴数据集读 /
  G145 ``__getattr__`` 下划线守卫。
"""
from __future__ import annotations

import json
import shutil
from typing import Any

import h5py
import numpy as np

__all__ = ["H5_FORMAT_PREFIX", "SimData", "TimeResult", "FreqResult",
           "IndexResult", "ensure_supported_format", "read_monitor",
           "open_result"]

#: 结果文件格式契约前缀（内核 driver 写根 attrs ``format`` =
#: ``"genie-results-h5/<minor>"``）。SDK 读数入口校验前缀——格式家族
#: 升级时明报，不静默按旧布局误读；缺 attrs 同样拒（真产物必有）。
H5_FORMAT_PREFIX = "genie-results-h5/"

#: 位置轴数据集名（R25：这些键是轴；场键永不按轴读）。
_AXES = ("x", "y", "z")

#: 域字母 → 词（错误措辞用；解析键保持内核的 f/t/i）。
_DOMAIN_WORDS = {"t": "time", "f": "freq", "i": "index"}


def _decode_str(v) -> str:
    """G144：h5py 变长串在部分写入配置下回读为 bytes——域标必 decode。"""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


def ensure_supported_format(h5) -> None:
    """根 attrs ``format`` 前缀守卫：坏前缀 / 缺 attrs 明报不猜。"""
    fmt = _decode_str(h5.attrs.get("format", ""))
    if not fmt.startswith(H5_FORMAT_PREFIX):
        raise RuntimeError(
            f"result file format not recognized: {fmt!r} (expected "
            f"prefix {H5_FORMAT_PREFIX!r}) — the file is either not a "
            f"GENIE result h5 or from an incompatible format family")


def monitors_manifest(h5) -> dict:
    """根 attrs ``monitors`` → ``{name: meta}``（G95 形态正源；坏 manifest
    不拦取数——shape 退回轴元素数猜测）。"""
    raw = h5.attrs.get("monitors", "")
    raw = _decode_str(raw) if isinstance(raw, (bytes, np.bytes_)) else raw
    try:
        parsed = json.loads(raw) if raw else []
        if isinstance(parsed, list):
            return {m.get("name"): m for m in parsed if isinstance(m, dict)}
    except ValueError:
        pass
    return {}


def _domain_of(group) -> str:
    return _decode_str(group.attrs.get("domain", ""))


def _shape_label(axes: dict, shape_hint) -> str:
    """形态标签：manifest 的 shape 优先（G95 正源——猜轴元素数对 index
    的逐格点云必报 volume、对真 surface 单格轴会低报，在场不猜）；
    缺失才按位置轴元素数计数（``n < 4`` 恒真）。"""
    if shape_hint in ("point", "line", "surface", "volume"):
        return shape_hint
    n = sum(1 for ax in _AXES if ax in axes and np.size(axes[ax]) > 1)
    return ("point", "line", "surface", "volume")[n]


def _n_positions(fields: dict) -> int:
    pos = 1
    for v in fields.values():
        if getattr(v, "ndim", 0) > 1:
            pos = max(pos, v.shape[-1])
    return pos


# ── 三类 Result（f.get_* 的返回形；纯数据容器零计算）────────────────

class _DataBase:
    """公共只读外壳：``.fields`` + 便捷面（keys/dict/[]）。"""

    fields: dict[str, np.ndarray]
    name: str
    shape: str

    def comps(self) -> list[str]:
        return list(self.fields)

    def keys(self) -> list[str]:
        return self.comps()

    def dict(self) -> dict[str, np.ndarray]:
        return dict(self.fields)

    def __contains__(self, key: str) -> bool:
        return key in self.fields

    def __getitem__(self, key: str) -> np.ndarray:
        """``r['Ey']`` ≡ ``r.fields['Ey']``。"""
        return self.fields[key]

    def __repr__(self) -> str:
        inner = ", ".join(
            f"{k}: {v.shape}" + (" complex" if np.iscomplexobj(v) else "")
            for k, v in self.fields.items())
        return (f"<{type(self).__name__} {self.name!r} "
                f"{self.shape}: {inner or '(empty)'}>")


class TimeResult(_DataBase):
    """时域监视器结果（SI）：``.time`` 秒；``.fields`` {分量: 数组}。"""

    def __init__(self, name: str, time: np.ndarray, fields: dict,
                 shape_hint=None, **ax: Any):
        self.name = name
        self.time = time
        self.fields = fields
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_samples = int(np.size(time))
        self.n_positions = _n_positions(fields)


class FreqResult(_DataBase):
    """频域监视器结果（SI）：``.freqs`` 赫兹；``.fields`` {分量: 复数数组}。

    点监视器的场保持 (n_freqs, 1) 尾轴；派生分析（透过率 / 通量）不在
    SDK 层——用裸分量自算（Q4.5 零计算零物理）。
    """

    def __init__(self, name: str, freqs: np.ndarray, fields: dict,
                 shape_hint=None, **ax: Any):
        self.name = name
        self.freqs = freqs
        self.fields = fields
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)
        self.n_freqs = int(np.size(freqs))
        self.n_positions = _n_positions(fields)


class IndexResult(_DataBase):
    """index 监视器结果：``.eps`` / ``.mu``（逐格点云）+ 每格 x/y/z 坐标。"""

    def __init__(self, name: str, fields: dict, shape_hint=None,
                 **ax: Any):
        self.name = name
        self.fields = fields
        self.eps = fields.get("eps")
        self.mu = fields.get("mu")
        self.x, self.y, self.z = ax.get("x"), ax.get("y"), ax.get("z")
        self.shape = _shape_label(
            {k: v for k, v in ax.items() if v is not None}, shape_hint)

    def comps(self) -> list[str]:
        return [k for k in ("eps", "mu") if self.fields.get(k) is not None]


# ── h5 → 对象解析（图 §1 唯一口径）──────────────────────────────────

def read_monitor(h5, domain: str, name: str | None = None):
    """按域选监视器组 → Result 对象（``domain ∈ {"t","f","i"}``）。

    缺省语义（Q4.5 单直多报）：该域恰一组直取；多组报错点名 ``name``。
    轴数据集（x/y/z/time/freqs）按轴读、其余按场读（R25）。
    """
    word = _DOMAIN_WORDS.get(domain, domain)
    ensure_supported_format(h5)
    cands = [k for k in h5.keys() if _domain_of(h5[k]) == domain]
    if name is None:
        if not cands:
            raise RuntimeError(
                f"no {word} monitor in this result h5 "
                f"(monitors: {', '.join(h5.keys()) or 'none'})")
        if len(cands) > 1:
            raise RuntimeError(
                f"multiple {word} monitors present "
                f"({', '.join(cands)}) — pass name explicitly")
        name = cands[0]
    elif name not in cands:
        raise RuntimeError(
            f"{word} monitor {name!r} not found; available {word} "
            f"monitors: {', '.join(cands) or 'none'}")

    grp = h5[name]
    axes: dict = {}
    fields: dict = {}
    for key in grp.keys():                       # R25：轴名键是轴，场键是场
        arr = np.asarray(grp[key][()])
        if key in _AXES or key in ("time", "freqs"):
            axes[key] = arr
        else:
            fields[key] = arr
    hint = monitors_manifest(h5).get(name, {}).get("shape")

    if domain == "t":
        if "time" not in axes:
            raise RuntimeError(
                f"time monitor {name!r} has no 'time' axis dataset")
        return TimeResult(name, axes.pop("time"), fields,
                          shape_hint=hint, **axes)
    if domain == "f":
        if "freqs" not in axes:
            raise RuntimeError(
                f"freq monitor {name!r} has no 'freqs' axis dataset")
        return FreqResult(name, axes.pop("freqs"), fields,
                          shape_hint=hint, **axes)
    return IndexResult(name, fields, shape_hint=hint, **axes)


# ── SimData（h5 惰性包装）───────────────────────────────────────────

class _MonitorView:
    """单监视器视图：属性/下标直达数据集（``data.tr.Ex`` → 惰性 Dataset）。

    ``owner`` = 所属 ``SimData`` 的强引用：链式访问里的临时 SimData 不会
    被 ``__del__`` 关掉 h5 文件（v1 G182 P5b：视图拿到死 group）。
    """

    def __init__(self, group, owner=None) -> None:
        self._g = group
        self._owner = owner

    def __getattr__(self, name: str):           # G145：下划线守卫防递归
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._g[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str):
        return self._g[name][()]                # 下标直接给 ndarray

    def keys(self) -> list[str]:
        return list(self._g.keys())

    def __dir__(self):
        return list(self._g.keys())


class SimData:
    """结果 h5 的惰性包装（组 = 监视器，数据集 = 场/轴）。

    ``data.<mon>.<field>`` 属性直达（惰性 h5py Dataset，切片或 ``[()]``
    才是 ndarray）；``data.attrs`` 根属性只读；``data.digest`` 只读根
    attrs（缺键 / 坏 JSON = None，等内核 b2 写侧——无 HTTP 回退）。
    close 三件套（with / close / ``__del__`` 兜底）；``scratch`` 目录
    （worker 的读缓存）归本对象——close 时连目录一起清。
    """

    def __init__(self, h5file, job_id: str = "", scratch=None) -> None:
        self._f = h5file
        self.job_id = job_id
        self._scratch = scratch

    def close(self) -> None:
        scratch = getattr(self, "_scratch", None)
        self._scratch = None
        try:
            self._f.close()
        except Exception:                       # noqa: BLE001 —— 已关不炸
            pass
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    def __enter__(self) -> "SimData":
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:                       # noqa: BLE001 —— 兜底不炸
            pass

    @property
    def attrs(self) -> dict:
        return dict(self._f.attrs)

    @property
    def digest(self):
        """根 attrs ``digest``（JSON）→ dict；缺键 / 坏 JSON → None。"""
        raw = self._f.attrs.get("digest")
        if raw is None or raw == "" or raw == b"":
            return None
        raw = _decode_str(raw) if isinstance(raw, (bytes, np.bytes_)) else raw
        try:
            parsed = json.loads(raw)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @property
    def monitors(self) -> list[str]:
        return list(self._f.keys())

    # -- 解析入口（与 f.get_* 同一口径——Q2.2-1 同成员同口径）----------

    def get_time(self, name: str | None = None) -> TimeResult:
        return read_monitor(self._f, "t", name)

    def get_freq(self, name: str | None = None) -> FreqResult:
        return read_monitor(self._f, "f", name)

    def get_index(self, name: str | None = None) -> IndexResult:
        return read_monitor(self._f, "i", name)

    def __getattr__(self, name: str):           # 监视器名直达；G145 守卫
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return _MonitorView(self._f[name], self)
        except KeyError as e:
            raise AttributeError(name) from e

    def __getitem__(self, name: str) -> _MonitorView:
        return _MonitorView(self._f[name], self)

    def __dir__(self):
        return sorted(set(list(super().__dir__()) + self.monitors))

    def __repr__(self) -> str:
        return (f"SimData(job_id={self.job_id!r}, monitors={self.monitors}, "
                f"attrs={self.attrs})")


def open_result(path, *, job_id: str = "", scratch=None) -> SimData:
    """h5 文件路径 → 格式守卫后的 SimData（惰性）。坏前缀：关句柄再抛。"""
    h5 = h5py.File(str(path), "r")
    try:
        ensure_supported_format(h5)
    except BaseException:
        h5.close()
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        raise
    return SimData(h5, job_id=job_id, scratch=scratch)
