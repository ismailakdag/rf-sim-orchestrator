from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None

from .common import JOB_ID_RE, ValidationError, sha256_file, validate_result_zip
from .host import HostServer, Store
from .worker import ApiClient, Worker, default_worker_id
from .capabilities import detect_cst_installations


def load_config(path: str) -> dict:
    with Path(path).open("rb") as handle:
        return tomllib.load(handle)


def token_from(args) -> str:
    token = getattr(args, "token", None) or os.environ.get("RF_SIM_TOKEN")
    if not token:
        raise SystemExit("Set RF_SIM_TOKEN or pass --token. Avoid storing tokens in committed files.")
    if len(token) < 32:
        raise SystemExit("API token must contain at least 32 characters")
    return token


def print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def command_host(args) -> None:
    config = load_config(args.config)
    host = config["host"]
    store = Store(Path(host["data_dir"]), int(host.get("lease_seconds", 120)), set(host["allowed_runners"]), int(host.get("max_result_bytes", 50 * 1024**3)), int(host.get("max_uncompressed_bytes", 100 * 1024**3)))
    server = HostServer((host.get("bind", "127.0.0.1"), int(host.get("port", 8765))), store, token_from(args))
    cert = host.get("tls_cert")
    key = host.get("tls_key")
    if bool(cert) != bool(key):
        raise SystemExit("tls_cert and tls_key must be configured together")
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"host listening on {server.server_address[0]}:{server.server_address[1]} with {len(store.allowed_runners)} allowed runner(s)")
    server.serve_forever()


def command_submit(args) -> None:
    client = ApiClient(args.url, token_from(args))
    with Path(args.job).open(encoding="utf-8") as handle:
        print_json(client.json("POST", "/api/v1/jobs", json.load(handle)))


def command_status(args) -> None:
    path = "/api/v1/jobs" + (f"/{args.job_id}" if args.job_id else "")
    print_json(ApiClient(args.url, token_from(args)).json("GET", path))


def command_requeue(args) -> None:
    if not JOB_ID_RE.fullmatch(args.job_id):
        raise SystemExit("invalid job_id")
    print_json(ApiClient(args.url, token_from(args)).json("POST", f"/api/v1/jobs/{args.job_id}/requeue", {"reason": args.reason}))


def command_resolve(args) -> None:
    if not JOB_ID_RE.fullmatch(args.job_id):
        raise SystemExit("invalid job_id")
    print_json(ApiClient(args.url, token_from(args)).json("POST", f"/api/v1/jobs/{args.job_id}/resolve", {"reason": args.reason}))


def worker_from_config(args) -> tuple[Worker, dict]:
    config = load_config(args.config)
    worker_config = config["worker"]
    root = Path(worker_config["data_dir"])
    worker = Worker(
        ApiClient(worker_config["host_url"], token_from(args)),
        worker_config.get("worker_id", default_worker_id()), root, config["runners"],
        int(worker_config.get("heartbeat_seconds", 30)),
        int(float(worker_config.get("min_free_gb", 0)) * 1024**3),
        bool(worker_config.get("cleanup_after_upload", False)),
        list(worker_config.get("cst_roots", [])),
    )
    return worker, config


def command_worker(args) -> None:
    worker, config = worker_from_config(args)
    worker_config = config["worker"]
    if args.once:
        print_json(worker.once() or {"state": "idle"})
    else:
        worker.loop(int(worker_config.get("poll_seconds", 10)))


def command_workers(args) -> None:
    print_json(ApiClient(args.url, token_from(args)).json("GET", "/api/v1/workers"))


def command_probe(args) -> None:
    config = load_config(args.config)
    worker_config = config["worker"]
    root = Path(worker_config["data_dir"]).resolve()
    disk_probe = root
    while not disk_probe.exists() and disk_probe.parent != disk_probe:
        disk_probe = disk_probe.parent
    usage = __import__("shutil").disk_usage(disk_probe)
    print_json({
        "worker_id": worker_config.get("worker_id", default_worker_id()),
        "host_url": worker_config["host_url"], "data_dir": str(root),
        "disk": {"free_bytes": usage.free, "total_bytes": usage.total, "free_gb": round(usage.free / 1024**3, 2)},
        "minimum_free_gb": float(worker_config.get("min_free_gb", 0)),
        "cleanup_after_upload": bool(worker_config.get("cleanup_after_upload", False)),
        "cst_installations": detect_cst_installations(list(worker_config.get("cst_roots", []))),
        "runners": sorted(config["runners"]),
    })


def command_worker_gui(args) -> None:
    from .gui import run_worker_gui
    run_worker_gui(args.config, getattr(args, "token", None))


def command_monitor_gui(args) -> None:
    from .gui import run_monitor_gui
    run_monitor_gui(args.url, token_from(args), args.local_current)


def command_results(args) -> None:
    if not JOB_ID_RE.fullmatch(args.job_id):
        raise SystemExit("invalid job_id")
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(args.url.rstrip("/") + f"/api/v1/results/{args.job_id}", headers={"Authorization": f"Bearer {token_from(args)}"})
    temp = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with urllib.request.urlopen(request, timeout=300) as response, temp.open("wb") as output:
            expected = response.headers.get("X-Content-SHA256")
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        observed = sha256_file(temp)
        if observed != expected:
            raise ValidationError("download SHA-256 differs from host header")
        verified = validate_result_zip(temp, args.job_id, args.max_uncompressed_bytes)
        os.replace(temp, destination)
        print_json({"job_id": args.job_id, "output": str(destination), "sha256": observed, "artifact_count": verified["file_count"], "verified": True})
    finally:
        if temp.exists():
            temp.unlink()


def parse_mac(value: str) -> bytes:
    compact = re.sub(r"[:-]", "", value)
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        raise ValidationError("MAC must contain exactly six hexadecimal octets")
    return bytes.fromhex(compact)


def command_wol(args) -> None:
    mac = parse_mac(args.mac)
    try:
        socket.inet_aton(args.broadcast)
    except OSError as exc:
        raise SystemExit("broadcast must be an IPv4 address") from exc
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be from 1 to 65535")
    packet = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (args.broadcast, args.port))
    print_json({"sent": True, "broadcast": args.broadcast, "port": args.port, "note": "Packet delivery and remote power-on are not guaranteed."})


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rf-sim", description="Durable host/worker RF simulation orchestration")
    sub = result.add_subparsers(dest="command", required=True)
    host = sub.add_parser("host", help="run the queue API host")
    host.add_argument("--config", required=True)
    host.add_argument("--token")
    host.set_defaults(func=command_host)
    for name, func in (("submit", command_submit), ("status", command_status), ("requeue", command_requeue), ("resolve", command_resolve), ("results", command_results)):
        item = sub.add_parser(name)
        item.add_argument("--url", required=True)
        item.add_argument("--token")
        if name == "submit":
            item.add_argument("job")
        elif name == "status":
            item.add_argument("job_id", nargs="?")
        elif name in {"requeue", "resolve"}:
            item.add_argument("job_id")
            reason_help = "operator evidence that no solver is active; closes the uncertain job without retry" if name == "resolve" else None
            item.add_argument("--reason", required=True, help=reason_help)
        else:
            item.add_argument("job_id")
            item.add_argument("--output", required=True)
            item.add_argument("--max-uncompressed-bytes", type=int, default=100 * 1024**3)
        item.set_defaults(func=func)
    worker = sub.add_parser("worker", help="run one outbound-polling worker")
    worker.add_argument("--config", required=True)
    worker.add_argument("--token")
    worker.add_argument("--once", action="store_true")
    worker.set_defaults(func=command_worker)
    workers = sub.add_parser("workers", help="show known workers and online state")
    workers.add_argument("--url", required=True)
    workers.add_argument("--token")
    workers.set_defaults(func=command_workers)
    probe = sub.add_parser("probe", help="inspect a worker configuration without contacting the host or launching CST")
    probe.add_argument("--config", required=True)
    probe.set_defaults(func=command_probe)
    worker_gui = sub.add_parser("worker-gui", help="open the school-computer worker control window")
    worker_gui.add_argument("--config", required=True)
    worker_gui.add_argument("--token")
    worker_gui.set_defaults(func=command_worker_gui)
    monitor_gui = sub.add_parser("monitor-gui", help="open the host worker/job monitor")
    monitor_gui.add_argument("--url", required=True)
    monitor_gui.add_argument("--token")
    monitor_gui.add_argument("--local-current", help="local Python campaign CURRENT.json to monitor read-only")
    monitor_gui.set_defaults(func=command_monitor_gui)
    wol = sub.add_parser("wol", help="send one Wake-on-LAN magic packet")
    wol.add_argument("--mac", required=True)
    wol.add_argument("--broadcast", required=True)
    wol.add_argument("--port", type=int, default=9)
    wol.set_defaults(func=command_wol)
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        args.func(args)
    except (ValidationError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
