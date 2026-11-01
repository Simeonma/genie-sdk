"""参数扫描编排（G29b 客户端组合；R24c run() 化重写）——
sweep = 编排层：把"跑一个仿真"原语（Simulation.run）用 N 次。

站长定案（R24c，"从最终代码简洁统一出发"）：提交一个作业的代码路径
只有 run() 一条。本模块不再有任何提交/轮询实现，只负责：

- **本地展开**：genie.models.sweep_expand（协议包同一求值核）逐点
  参数代入 + 表达式求值 → N 份纯数值配置（SweepExpandError 在
  f.sweep() 调用点即抛，对应旧 POST 422 面）；
- **窗口执行**：每点 = 一个独立 Simulation 实例走 run() 全原语——
  本地校验、auto-save（逐点 `.gensim` 点文档落扫描文件夹）、visualize/结果面
  随原语免费。max_parallel = 1（缺省）零线程顺序执行；> 1 线程池，
  每线程独占一个实例（run 内部 K4 锁为实例级，无共享状态）；
- **abort（G89 重写）**：首个坏终态停排新点 + 取消在途——不再有
  "首个成功点之后失效"的窗口；缺省 continue = 坏点记 failed 继续排；
- **错误归属（R37）**：点的配置错（GenieConfigError）/作业坏终态
  （GenieJobError）= **点的事**，按 abort/continue 策略处理；其它异常
  （凭据失效、传输断开、网关拒绝）= **环境的事**，第一次出现就停排，
  `wait()` 原样抛出一次——一把坏 key 不再变成 N 个失败点；
- **gpus_per_sim（G90 构造性修复）**：每点 run(gpu=...)——旋钮真实生效；
- **结果 = 立方**（G182 P5b，站长 2026-10-30 裁）：扫描不新增结果面——
  N 份点结果摞成**一份普通结果**（``results()``）：每个字段数组前加
  k 维（k = 扫描轴数，不设上限；轴序 = 轴序声明序），坐标
  （freqs/time/x/y/z）一条轴仍是一个向量，失败点 = NaN 切片。
  取单点就是 ``r.tr.T[i]``（numpy 切片），不另设逐点入口。
  transmission 等换算依赖内核 source 谱归一——物理不过 SDK（铁律）。

铁律沿用：零计算零物理——SDK 只做线格式搬运与编排，仿真永远是内核的事。

（T5 结构重构：core/sweep.py → core/sweep/ 包；聚合再导出，
import 面不变——cube.py 共享实现 / sweepset.py 只读集合 / handle.py 活编排。）
"""
from .handle import (SWEEP_TERMINAL_BAD, SWEEP_TERMINAL_OK, SweepHandle,
                     _read_param, active_handles, hooks, new_sweep_dir)
from .sweepset import SweepSet
from .cube import MANIFEST_NAME, _cube_bytes, _merge_cube, _results, _visualize

__all__ = [
    "SWEEP_TERMINAL_BAD", "SWEEP_TERMINAL_OK", "SweepHandle", "SweepSet",
    "MANIFEST_NAME", "active_handles", "hooks", "new_sweep_dir",
    "_merge_cube",
]
