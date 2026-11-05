# GENIE SDK

用 Python 写光子 FDTD 仿真：脚本建模 → 一键提交云端 GPU → 结果随档
`.gensim` → `post_analyze` 自定义后处理随档分享。

## 安装

需要 Python ≥ 3.10：

```
pip install genie-sdk
```

预发布期也可从源码装：

```
pip install git+https://github.com/Simeonma/genie-sdk.git
```

## 连接云端（怎么连网址工作）

GENIE 的仿真跑在云端 GPU 集群，服务入口 `https://genie-optics.com`。
你需要一个服务 token（向管理员申请），登录一次即可——凭据只写本地
`~/.genie/credentials.json`（权限 0600），不会上传别处：

```
genie login <你的token>
```

- 换网关：设环境变量 `GENIE_BACKEND=https://…` 再启动（缺省即
  genie-optics.com）；
- 撤销凭据：`genie logout`。

## 启动 Studio

```
genie studio
```

启动后把浏览器打开它打印的地址（形如 `http://127.0.0.1:<端口>`）——
左上是脚本编辑器，直接写 Python 驱动模型；`Ctrl+C` 退出。指定端口用
`genie studio --port 8777`（缺省系统自动分配）。

最小脚本（贴进编辑器运行）：

```python
f.domain.x_range = (-1e-6, 1e-6)
f.domain.y_range = (-1e-6, 1e-6)
f.domain.z_range = (-1e-6, 1e-6)
f.domain.total_time = 60e-15
f.mesh.dx = 40e-9
f.mat.add_lorentz(f.mat_from_nk("Au"))        # 材料库一条龙
f.geom.add_box("wg")
f.geom["wg"].material = "Au"
f.geom["wg"].x_range = (-5e-7, 5e-7)
f.geom["wg"].y_range = (-5e-7, 5e-7)
f.geom["wg"].z_range = (-5e-7, 5e-7)
f.src.add_plane_wave("src")
f.src["src"].freq = 300e12
f.src["src"].bandwidth = 60e12
f.mon.add_freq("tr")
f.mon["tr"].field = "Ex"
f.mon["tr"].freq = {"center": 300e12, "bw": 120e12, "n": 200}

f.submit()          # 提交云端，结果自动存回本地 .gensim
r = f.results()     # 读结果（SimData）
```

## 命令速查

| 命令 | 作用 |
|---|---|
| `genie login <token>` | 登录云端（写本地凭据） |
| `genie studio` | 启动本地 Studio（裸 `genie` 同义） |
| `genie connect` | 报告当前 Studio 地址与 token（agent 接入用） |
| `genie logout` | 撤销云凭据 |

完整文档建设中（1.0.0 alpha）。问题反馈：你的服务管理员。
