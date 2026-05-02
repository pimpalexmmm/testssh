import os, time, base64, select, threading, socket, struct, socketserver
from github import Github

# -------------------------- SOCKS5 سرور ساده --------------------------
class Socks5Handler(socketserver.BaseRequestHandler):
    def handle(self):
        # 1. greeting
        ver, nm = struct.unpack("!BB", self.request.recv(2))
        if ver != 5: return
        self.request.recv(nm)
        self.request.sendall(struct.pack("!BB", 5, 0))

        # 2. request
        ver, cmd, _, atype = struct.unpack("!BBBB", self.request.recv(4))
        if ver != 5 or cmd != 1: return
        if atype == 1:  # IPv4
            addr = socket.inet_ntoa(self.request.recv(4))
        elif atype == 3:  # domain
            length = ord(self.request.recv(1))
            addr = self.request.recv(length).decode()
        else: return
        port = struct.unpack("!H", self.request.recv(2))[0]

        # connect to remote
        try:
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.connect((addr, port))
        except:
            self.request.sendall(struct.pack("!BBBBIH", 5, 1, 0, 1, 0, 0))
            return

        # success reply
        self.request.sendall(struct.pack("!BBBB", 5, 0, 0, 1) + b'\x00\x00\x00\x00' + struct.pack("!H", 0))
        self.request.settimeout(30)
        remote.settimeout(30)

        # relay
        try:
            while True:
                r, _, _ = select.select([self.request, remote], [], [], 30)
                if self.request in r:
                    d = self.request.recv(4096)
                    if not d: break
                    remote.sendall(d)
                if remote in r:
                    d = remote.recv(4096)
                    if not d: break
                    self.request.sendall(d)
        finally:
            self.request.close()
            remote.close()

class ThreadedSocks5Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

# -------------------------- رِلِی گیت‌هاب --------------------------
def relay():
    token = os.environ['RELAY_TOKEN']
    issue_c2s = int(os.environ['ISSUE_C2S'])
    issue_s2c = int(os.environ['ISSUE_S2C'])

    g = Github(token)
    repo = g.get_repo(os.environ['GITHUB_REPOSITORY'])
    c2s = repo.get_issue(issue_c2s)
    s2c = repo.get_issue(issue_s2c)

    sessions = {}         # sid -> socket
    last_comment_id = 0

    print("[سرور] آماده دریافت دستورات از Issue کلاینت...")

    while True:
        try:
            # خواندن کامنت‌های جدید از client->server
            comments = c2s.get_comments()
            for comment in comments:
                if comment.id <= last_comment_id:
                    continue
                last_comment_id = comment.id
                body = comment.body.strip()
                parts = body.split(':', 3)
                if len(parts) < 3:
                    continue

                sid = parts[1]
                cmd = parts[2]

                if cmd == 'CONNECT':
                    host = parts[3]
                    port = int(parts[4])
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.connect((host, port))
                        s.setblocking(False)
                        sessions[sid] = s
                        s2c.create_comment(f"SESSION:{sid}:CONNECTED")
                    except Exception as e:
                        s2c.create_comment(f"SESSION:{sid}:ERROR:{e}")

                elif cmd == 'DATA':
                    data = base64.b64decode(parts[3])
                    if sid in sessions:
                        try:
                            sessions[sid].send(data)
                        except:
                            pass

                elif cmd == 'CLOSE':
                    if sid in sessions:
                        sessions[sid].close()
                        del sessions[sid]

        except Exception as e:
            print(f"خطا در خواندن کامنت: {e}")

        # ارسال پاسخ‌ها به server->client
        for sid, sock in list(sessions.items()):
            try:
                ready, _, _ = select.select([sock], [], [], 0)
                if ready:
                    data = sock.recv(4096)
                    if not data:
                        s2c.create_comment(f"SESSION:{sid}:CLOSED")
                        sock.close()
                        del sessions[sid]
                        continue
                    b64 = base64.b64encode(data).decode()
                    s2c.create_comment(f"SESSION:{sid}:DATA:{b64}")
            except BlockingIOError:
                pass
            except:
                if sid in sessions:
                    sock.close()
                    del sessions[sid]

        time.sleep(0.3)

if __name__ == '__main__':
    # اجرای سرور SOCKS5 در پس‌زمینه
    server = ThreadedSocks5Server(('0.0.0.0', 1080), Socks5Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1)
    relay()
