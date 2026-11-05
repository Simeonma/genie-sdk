"""genie.host — the Studio process (browser HTTP surface / cloud credential
store / the sole gateway egress).

Six modules: app / gw / cred / script / card / cli; the session lives in
this process (the host talks only to the session, no importing ipc /
worker / core).
"""
