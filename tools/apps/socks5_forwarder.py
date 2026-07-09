"""SOCKS5 代理转发器。

在本地启动一个无认证的 SOCKS5 代理（Chrome 可直接使用），
将请求转发到需要认证的远程韩国 SOCKS5 代理。

Chromium 不支持带认证的 SOCKS5，所以需要这个中间层。
"""

import os
import select
import socket
import struct
import sys
import threading
import time

# 启动时加载 tools/.env(若存在),让凭据可集中配置
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _env import load_env  # noqa: E402
load_env()

# ---- 配置 ----
# 远程代理凭据从环境变量读取,避免明文硬编码泄露。
# 设置示例(写入 ~/.zshrc 或运行前 export):
#   export CB_REMOTE_PROXY_HOST=101.55.16.42
#   export CB_REMOTE_PROXY_PORT=1337
#   export CB_REMOTE_PROXY_USER=...
#   export CB_REMOTE_PROXY_PASS=...
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 11080
REMOTE_HOST = os.environ.get("CB_REMOTE_PROXY_HOST", "")
REMOTE_PORT = int(os.environ.get("CB_REMOTE_PROXY_PORT", "0"))
REMOTE_USER = os.environ.get("CB_REMOTE_PROXY_USER", "")
REMOTE_PASS = os.environ.get("CB_REMOTE_PROXY_PASS", "")

if not (REMOTE_HOST and REMOTE_PORT and REMOTE_USER and REMOTE_PASS):
    sys.stderr.write(
        "缺少远程代理环境变量。请设置:\n"
        "  export CB_REMOTE_PROXY_HOST=...\n"
        "  export CB_REMOTE_PROXY_PORT=...\n"
        "  export CB_REMOTE_PROXY_USER=...\n"
        "  export CB_REMOTE_PROXY_PASS=...\n"
    )
    sys.exit(1)


def relay(a: socket.socket, b: socket.socket):
    """在 a 和 b 之间双向转发，任一方向断开则停止。"""
    sockets = [a, b]
    try:
        while True:
            readable, _, _ = select.select(sockets, [], [], 30)
            if not readable:
                break
            for sock in readable:
                data = sock.recv(8192)
                if not data:
                    return
                other = b if sock is a else a
                other.sendall(data)
    except Exception:
        pass
    finally:
        try:
            a.close()
        except Exception:
            pass
        try:
            b.close()
        except Exception:
            pass


def socks5_connect_to_remote(target_host: str, target_port: int) -> socket.socket:
    """通过远程 SOCKS5 代理（带认证）连接到目标主机。"""
    sock = socket.create_connection((REMOTE_HOST, REMOTE_PORT), timeout=15)

    # SOCKS5 握手（带认证）
    sock.sendall(b"\x05\x01\x02")  # VER=5, 1 method, METHOD=2 (username/password)
    resp = sock.recv(2)
    if resp != b"\x05\x02":
        raise Exception(f"SOCKS5 握手失败: {resp.hex()}")

    # 发送认证信息
    user_bytes = REMOTE_USER.encode()
    pass_bytes = REMOTE_PASS.encode()
    auth_msg = bytes([1, len(user_bytes)]) + user_bytes + bytes([len(pass_bytes)]) + pass_bytes
    sock.sendall(auth_msg)
    auth_resp = sock.recv(2)
    if auth_resp[1] != 0:
        raise Exception(f"SOCKS5 认证失败: {auth_resp.hex()}")

    # 发送 CONNECT 请求（域名模式）
    host_bytes = target_host.encode()
    req = b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack(">H", target_port)
    sock.sendall(req)
    resp = sock.recv(10)
    if resp[1] != 0:
        raise Exception(f"SOCKS5 CONNECT 失败: {resp.hex()}")

    return sock


def handle_client(client: socket.socket, addr: tuple):
    """处理一个来自 Chrome 的 SOCKS5 连接（无认证）。"""
    try:
        # 1. SOCKS5 握手（无认证）
        greeting = client.recv(2)
        if len(greeting) < 2 or greeting[0] != 5:
            client.close()
            return

        nmethods = greeting[1]
        client.recv(nmethods)
        client.sendall(b"\x05\x00")  # 无认证

        # 2. 读取 CONNECT 请求
        req = client.recv(4)
        if len(req) < 4:
            client.close()
            return

        ver, cmd, rsv, atyp = req
        if cmd != 1:  # 只支持 CONNECT
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            client.close()
            return

        # 3. 解析目标地址
        if atyp == 1:  # IPv4
            target = socket.inet_ntop(socket.AF_INET, client.recv(4))
        elif atyp == 3:  # 域名
            target_len = client.recv(1)[0]
            target = client.recv(target_len).decode()
        elif atyp == 4:  # IPv6
            target = socket.inet_ntop(socket.AF_INET6, client.recv(16))
        else:
            client.close()
            return

        target_port = struct.unpack(">H", client.recv(2))[0]
        print(f"[{time.strftime('%H:%M:%S')}] {addr[0]} → {target}:{target_port}")

        # 4. 连接远程 SOCKS5
        remote = socks5_connect_to_remote(target, target_port)

        # 5. 回复 Chrome 连接成功
        client.sendall(b"\x05\x00\x00\x01" + b"\x00\x00\x00\x00" + b"\x00\x00")

        print(f"    隧道已建立，开始转发...")

        # 6. 双向转发
        relay(client, remote)

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] 错误: {e}")
    finally:
        try:
            client.close()
        except Exception:
            pass


def main():
    print(f"本地转发: {LOCAL_HOST}:{LOCAL_PORT} → {REMOTE_HOST}:{REMOTE_PORT} (SOCKS5 + 认证)")
    print("Chrome 代理设置为 socks5://127.0.0.1:11080 (无需认证)")
    print("按 Ctrl+C 停止\n")

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LOCAL_HOST, LOCAL_PORT))
    server.listen(50)
    server.settimeout(1)  # 每秒超时一次，以便响应终止信号
    print(f"等待连接...")

    running = True
    try:
        while running:
            try:
                client, addr = server.accept()
                t = threading.Thread(target=handle_client, args=(client, addr), daemon=True)
                t.start()
            except socket.timeout:
                continue
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.close()


if __name__ == "__main__":
    main()
