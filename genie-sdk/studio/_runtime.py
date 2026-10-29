"""会话 worker 的运行时缝——唯一知道"worker 是个进程"的代码。

上层 ``SessionKernel`` 只认这张方法表（协议解析、输出环、在途账本仍在它
自己身上）：

    rt.start()              起 worker（本机 = spawn 子进程）
    rt.send(obj) -> bool    发一条协议消息；False = 通道断了
    rt.alive -> bool        worker 还活着吗
    rt.stop()  -> bool      打断当前执行（SIGINT / CTRL_BREAK），不杀进程
    rt.kill()               回收 worker（重生或停机时用）

本机实现 = :class:`PipeRuntime`：``Popen`` + stdout/stderr 两个读线程 +
管道协议（一行一个 JSON 帧）。网页实现 = ``InprocRuntime``：Pyodide 里
既没有进程也没有线程，所以直接同进程调 ``kernel_worker.serve()``，见
`LEDGER.md` G80 第 4 步。

**为什么这层必须独立**：浏览器没有 ``subprocess`` / ``threading``。把
"起进程 + 读管道"留在 ``SessionKernel`` 里，网页形态就只能复制一份会话
管理——那正是 G80 要避免的第二真源。
"""
from __future__ import annotations

import json
import os
import sys
import signal
import subprocess
import threading
from typing import Any, Callable


class PipeRuntime:
    """子进程形态：``Popen`` + 两个读线程 + 管道写。

    三个回调都由**读线程**调用（实现方自己负责加锁）：

    - ``on_line(str)``   stdout 每来一行（含空行，过滤器在调用方）
    - ``on_err(str)``    stderr 每来一行
    - ``on_exit()``      stdout EOF = worker 退出（只喊一次）
    """

    def __init__(self, cmd: list[str], *, env: dict, cwd: str,
                 on_line: Callable[[str], None],
                 on_err: Callable[[str], None],
                 on_exit: Callable[[], None]) -> None:
        self._cmd = cmd
        self._env = env
        self._cwd = str(cwd)
        self._on_line = on_line
        self._on_err = on_err
        self._on_exit = on_exit
        self._wlock = threading.Lock()      # 串行化 stdin 写
        self.proc: subprocess.Popen | None = None

    # ---- 生命周期 ----

    def start(self) -> None:
        # Windows：独立进程组 → stop() 才能发 CTRL_BREAK_EVENT（G33）
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            bufsize=1, env=self._env, cwd=self._cwd,
            creationflags=flags,
        )
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self) -> None:
        proc = self.proc
        for line in proc.stdout:
            self._on_line(line)
        self._on_exit()

    def _read_err(self) -> None:
        proc = self.proc
        for line in proc.stderr:
            self._on_err(line)

    # ---- 通道 ----

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send(self, obj: dict) -> bool:
        if not self.alive or self.proc.stdin is None:
            return False
        try:
            with self._wlock:
                self.proc.stdin.write(
                    json.dumps(obj, ensure_ascii=False) + "\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            return False
        return True

    # ---- 控制 ----

    def stop(self) -> bool:
        """打断当前执行（不杀进程）：worker 侧 exec 期掐断、空闲期忽略。"""
        if not self.alive:
            return False
        try:
            if os.name == "nt":
                # Windows 不支持 send_signal(SIGINT)（ValueError: Unsupported
                # signal）。worker 起在独立进程组里，发 CTRL_BREAK_EVENT →
                # 子进程 SIGBREAK → kernel_worker 里同一处理器掐断（G33）。
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            return False
        return True

    def kill(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass


class InprocRuntime:
    """同进程形态（Pyodide）：没有进程、没有线程，由调用方逐条驱动。

    ``start()`` 让 ``kernel_worker`` 建好会话状态并把 ``_step`` 交出来
    （``main(..., expose=…)``）；``send()`` 把一条协议消息交给**同一个**
    ``_step``。执行逻辑与子进程形态是同一份代码——差别只有"谁来循环"。
    """

    def __init__(self, cmd=None, *, env=None, cwd=None,
                 on_line: Callable[[str], None],
                 on_err: Callable[[str], None],
                 on_exit: Callable[[], None]) -> None:
        self._on_line = on_line
        self._on_err = on_err
        self._on_exit = on_exit
        self._step = None
        self._alive = False

    def start(self) -> None:
        from . import kernel_worker
        # 同进程形态会改写 core 的注入面（simulate/sweep 的 hooks）——先把
        # 宿主那份存下来，kill() 原样还回去。不还 = 同进程里别的消费者
        # 继承了 worker 的注入面（测试串味；宿主里会变成双份事件/双写）。
        from genie.core import simulate as _sim
        from genie.core import sweep as _swp
        self._hooks = (dict(_sim.hooks), dict(_swp.hooks))
        box: dict = {}
        kernel_worker.main(emit_fn=self._emit,
                           expose=lambda step: box.setdefault("step", step))
        self._step = box.get("step")
        if self._step is None:
            raise RuntimeError("kernel_worker 没交出 _step")
        self._alive = True

    def _emit(self, ev: dict) -> None:
        # 与子进程形态的行协议**逐字相同**：一行 JSON（default=repr 同款），
        # 于是 SessionKernel 的 _pump 解析逻辑两种形态共用。
        self._on_line(json.dumps(ev, ensure_ascii=False, default=repr) + "\n")

    @property
    def alive(self) -> bool:
        return self._alive

    @property
    def proc(self):
        return None                    # 同进程：没有进程句柄

    def send(self, obj: dict) -> bool:
        if not self._alive or self._step is None:
            return False
        try:
            self._step(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception as e:         # noqa: BLE001 —— 会话不能静默死
            self._on_err(f"{e!r}\n")
            return False
        return True

    def stop(self) -> bool:
        return False                   # 没有信号可用；浏览器里用 terminate()

    def kill(self) -> None:
        hooks = getattr(self, "_hooks", None)
        if hooks:
            from genie.core import simulate as _sim
            from genie.core import sweep as _swp
            for mod, saved in ((_sim, hooks[0]), (_swp, hooks[1])):
                mod.hooks.clear()
                mod.hooks.update(saved)
            self._hooks = None
        self._alive = False


def make_runtime(cmd, **kw: Any):
    """按运行环境选实现（**分支在这里，不在调用方**）。

    浏览器（Pyodide/WASM）没有子进程，用同进程的 ``InprocRuntime``；
    其余环境 spawn 子进程。
    """
    if sys.platform == "emscripten":
        return InprocRuntime(cmd, **kw)
    return PipeRuntime(cmd, **kw)
