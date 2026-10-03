#!/usr/bin/env python3
"""Exercise rendered Nginx routing with isolated localhost HTTP and DNS servers.

Requires pinned Helm, PyYAML and NGINX pointing to an Nginx 1.30.x binary.
No Kubernetes, tailnet, cloud credentials or system configuration is used.
"""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time

import yaml

ROOT = Path(__file__).resolve().parents[2]


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Upgrade') == 'websocket':
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.end_headers()
            self.close_connection = True
            return
        body = json.dumps({'backend': self.server.server_address[0], 'path': self.path,
                           'host': self.headers.get('Host'),
                           'scheme': self.headers.get('X-Forwarded-Proto'),
                           'forwarded_for': self.headers.get('X-Forwarded-For')}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def request(port, host='echo.internal.likelion.uk', path='/', headers=None):
    # Longer than the chart's connect timeout plus resolver timeout.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.request('GET', path, headers={'Host': host, **(headers or {})})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def main():
    nginx = os.environ['NGINX']
    helm = os.environ.get('HELM', 'helm')
    assert 'nginx/1.30.' in subprocess.run([nginx, '-v'], capture_output=True, text=True).stderr
    dns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dns.bind(('127.0.0.1', 0))
    dns.settimeout(0.1)
    stop = threading.Event()
    address = ['127.0.0.1']

    def answer_dns():
        while not stop.is_set():
            try:
                packet, peer = dns.recvfrom(4096)
            except socket.timeout:
                continue
            end = 12
            while packet[end]:
                end += 1 + packet[end]
            end += 5  # zero label, QTYPE and QCLASS
            question = packet[12:end]
            response = packet[:2] + struct.pack('!HHHHH', 0x8180, 1, 1, 0, 0) + question
            response += b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 1, 4) + socket.inet_aton(address[0])
            dns.sendto(response, peer)

    dns_thread = threading.Thread(target=answer_dns, daemon=True)
    first = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    upstream_port = first.server_address[1]
    threading.Thread(target=first.serve_forever, daemon=True).start()
    dns_thread.start()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        gateway_port = reservation.getsockname()[1]
    process = None
    try:
        with tempfile.TemporaryDirectory(prefix='iris-gateway-runtime-') as temporary:
            directory = Path(temporary)
            rendered = subprocess.check_output([
                helm, 'template', 'iris-onprem-gateway', str(ROOT / 'helm/charts/iris-onprem-gateway'),
                '--kube-version', '1.35.0', '--namespace', 'onprem-gateway',
                '-f', str(ROOT / 'helm/charts/iris-onprem-gateway/ci/private-values.yaml')], text=True)
            config = next(d for d in yaml.safe_load_all(rendered) if d and d['kind'] == 'ConfigMap')['data']['nginx.conf']
            # Only local sockets, temporary paths and a short DNS TTL differ from the rendered configuration.
            config = config.replace('worker_processes auto;', 'worker_processes 1;')
            config = config.replace('listen 8080 default_server;', f'listen 127.0.0.1:{gateway_port} default_server;')
            config = config.replace('listen 8080;', f'listen 127.0.0.1:{gateway_port};')
            config = config.replace('kube-dns.kube-system.svc.cluster.local', f'127.0.0.1:{dns.getsockname()[1]}')
            config = config.replace('valid=30s', 'valid=1s')
            config = config.replace('svc.cluster.local:80', f'svc.cluster.local:{upstream_port}')
            config = config.replace('/tmp/', str(directory) + '/')
            config = config.replace('/dev/stdout', str(directory / 'access.log'))
            config = config.replace('/dev/stderr', str(directory / 'error.log'))
            config_path = directory / 'nginx.conf'
            config_path.write_text(config)
            subprocess.run([nginx, '-t', '-p', str(directory) + '/', '-c', str(config_path)], check=True, capture_output=True)
            with (directory / 'nginx.log').open('w') as logs:
                process = subprocess.Popen([nginx, '-p', str(directory) + '/', '-c', str(config_path), '-g', 'daemon off;'], stdout=logs, stderr=logs)
                deadline = time.monotonic() + 5
                while True:
                    try:
                        assert request(gateway_port, path='/healthz')[0] == 200
                        break
                    except ConnectionRefusedError:
                        assert time.monotonic() < deadline, 'Nginx failed to start'
                        time.sleep(0.05)
                status, _, raw = request(gateway_port, path='/nested/path?q=a%20b&n=2',
                                         headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '203.0.113.9'})
                body = json.loads(raw)
                assert status == 200 and body['backend'] == '127.0.0.1'
                assert body['path'] == '/nested/path?q=a%20b&n=2' and body['host'] == 'echo.internal.likelion.uk'
                assert body['scheme'] == 'https' and body['forwarded_for'] == '203.0.113.9, 127.0.0.1'
                assert json.loads(request(gateway_port, headers={'X-Forwarded-Proto': 'http'})[2])['scheme'] == 'http'
                assert json.loads(request(gateway_port)[2])['scheme'] == 'https'
                assert json.loads(request(gateway_port, headers={'X-Forwarded-Proto': 'malicious'})[2])['scheme'] == 'https'
                for host in ('unknown.test', 'echo.internal.likelion.uk.evil.test', 'echoXinternalXlikelionXuk', 'internal.likelion.uk', '-echo.internal.likelion.uk', 'echo-.internal.likelion.uk', 'a'*64+'.internal.likelion.uk', 'nested.echo.internal.likelion.uk'):
                    assert request(gateway_port, host=host)[0] == 404, host
                status, headers, _ = request(gateway_port, headers={'Upgrade': 'websocket', 'Connection': 'Upgrade'})
                assert status == 101 and headers['Upgrade'] == 'websocket'
                # DNS moves to an unavailable loopback address, then recovers, without a gateway restart.
                address[0] = '127.0.0.2'
                deadline = time.monotonic() + 8
                while request(gateway_port)[0] not in (502, 504):
                    assert time.monotonic() < deadline, 'Upstream loss did not produce a bounded error'
                    time.sleep(0.1)
                address[0] = '127.0.0.1'
                deadline = time.monotonic() + 8
                while request(gateway_port)[0] != 200:
                    assert time.monotonic() < deadline, 'Nginx did not re-resolve the recovered proxy IP'
                    time.sleep(0.1)
                assert process.poll() is None, 'Gateway must survive upstream failure'
    finally:
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
        first.shutdown()
        first.server_close()
        stop.set()
        dns_thread.join(timeout=1)
        dns.close()
    print('Nginx syntax, Host/path/query/TLS headers, unknown Host, WebSocket, DNS re-resolution and upstream failure: passed. No cluster deployment tested.')


if __name__ == '__main__':
    main()
