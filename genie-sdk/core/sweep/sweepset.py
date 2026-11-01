"""磁盘上的一个扫描（G182 P5）：读 ``sweep.json`` + 点文档（只读面）。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .cube import MANIFEST_NAME, _cube_bytes, _results, _visualize


class SweepSet:
    """磁盘上的一个扫描（G182 P5）：读 ``sweep.json`` + 点文档。

    **不打开任何仿真实例**（站长 2026-10-30 裁：读取只要聚合结果）——
    公开入口 ``genie.open_sweep(dir)``。可用于新会话/另一台机器。
    """

    def __init__(self, sweep_dir):
        p = Path(sweep_dir).expanduser()
        if not p.is_dir():
            raise ValueError(f"扫描文件夹不存在：{p}")
        mp = p / MANIFEST_NAME
        if not mp.is_file():
            raise ValueError(
                f"不是扫描文件夹（缺 {MANIFEST_NAME}）：{p}")
        try:
            data = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"扫描索引读不了（{mp}）：{e}") from e
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError(f"扫描索引版本不支持：{mp}")
        self.dir = p
        self.manifest = data
        self.name = str(data.get("name") or "")
        self.axes = data.get("axes") or []
        self.state = str(data.get("state") or "")
        self.created = str(data.get("created") or "")
        self.settings = dict(data.get("settings") or {})
        self.points = list(data.get("points") or [])
        self.axis_names = [a.get("name") for a in self.axes
                           if isinstance(a, dict) and a.get("name")]
        if not self.axis_names and self.points:
            self.axis_names = list(self.points[0].get("params") or {})

    # ---- 元数据 ----

    @property
    def id(self) -> str:
        """扫描 id（sweep.json 的 ``sweep_id``）——visualize 的结果键。"""
        return str(self.manifest.get("sweep_id") or "")

    def doc_path(self, i: int) -> Path | None:
        """第 i 点的文档路径（文件夹内相对名 → 绝对；无 → None）。"""
        name = str((self.points[i] or {}).get("doc") or "")
        return (self.dir / name) if name else None

    def rollup(self) -> dict:
        c: dict = {}
        for p in self.points:
            key = str(p.get("state") or "pending")
            c[key] = c.get(key, 0) + 1
        return c

    def status(self) -> dict:
        return {"sweep_id": self.manifest.get("sweep_id", ""),
                "state": self.state, "dir": str(self.dir),
                "rollup": self.rollup(),
                "points": [{"idx": i, "state": p.get("state"),
                            "job_id": p.get("job_id"),
                            "doc": p.get("doc")}
                           for i, p in enumerate(self.points)]}

    # ---- 结果（扫描 = 结果多 k 维；只读文档，零 HTTP）----

    @property
    def params(self) -> dict:
        """{轴名: 逐点取值}——轴序 = ``axis_names``（笛卡尔积序）。"""
        return {a: np.asarray([(p.get("params") or {}).get(a, np.nan)
                               for p in self.points], dtype=float)
                for a in self.axis_names}

    def _point_bytes(self, i: int):
        """第 i 点的结果字节（失败/无文档/坏文档 → None ⇒ NaN 切片）。"""
        from ..archive import simfile
        doc = self.doc_path(i)
        if doc is None or not doc.is_file():
            return None
        try:
            return simfile.result_bytes(doc)
        except Exception:   # noqa: BLE001 —— 坏点文档 → NaN 切片
            return None

    def results(self):
        """扫描结果 → ``SimData``（与单跑同一个类型，数据多 k 维）。

        ``r.tr.T`` 形状 = (n1, …, nk) + 点内形状（轴序 = ``axes``）；
        坐标（``r.tr.freqs``）不复制；取单点就是 ``r.tr.T[i]``。
        """
        return _results(self)

    def _cube_bytes(self) -> bytes:
        return _cube_bytes(self)

    def visualize(self, mon: str, field: str | None = None) -> None:
        """把扫描结果推到 Studio 的 Plot 面板（与 ``f.visualize`` 同款）。

        推的是合并后的结果（数据多 k 维）——面板把参数轴当普通轴。
        无会话 = 明确报错（无本地画图退化轨，G148）。
        """
        return _visualize(self, mon, field)

    def __repr__(self) -> str:  # pragma: no cover —— 调试便利
        return f"<SweepSet {self.name!r} {self.state} dir={self.dir}>"
