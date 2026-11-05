#!/usr/bin/env python3
"""Small, interactive Codex connectivity check; return only allowlisted diagnostics."""
import argparse
import concurrent.futures
import datetime
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import uuid


def probe(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return {"url": url, "http_status": response.status}
    except urllib.error.HTTPError as exc:
        return {"url": url, "http_status": exc.code}
    except Exception as exc:
        return {"url": url, "error_type": type(exc).__name__}


def classify(output):
    # Never return free-form logs, credentials, server response bodies or env values.
    patterns = {
        "authentication": r"unauthorized|not logged in|authentication|invalid.api.key",
        "permission_or_region": r"forbidden|unsupported.country|unsupported.region|access.denied",
        "quota_or_rate_limit": r"quota|rate.limit|usage.limit|too.many.requests",
        "model_access": r"model.*(?:not.found|not.supported|does.not.exist|access)",
        "network": r"connection|timed?.out|dns|resolve.host|stream.disconnected",
        "tls": r"certificate|tls|ssl",
        "sandbox": r"sandbox|landlock|bwrap|operation.not.permitted",
        "cli_arguments": r"unexpected.argument|unrecognized|unknown.option",
    }
    return [name for name, pattern in patterns.items() if re.search(pattern, output, re.I)]


def run_check(args, report, output):
    urls = ["https://auth.openai.com/", "https://chatgpt.com/", "https://api.openai.com/v1/models"]
    if not args.skip_network_probe:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            report["network"] = list(pool.map(probe, urls))
        print("HTTP 探测完成；401/403 不能用于判断 Codex 是否可用。", flush=True)
    cli = shutil.which("codex")
    fallback = Path.home() / ".local/bin/codex"
    if not cli and fallback.is_file():
        cli = str(fallback)
    if not cli and not args.probe_only:
        curl = shutil.which("curl")
        if not curl:
            report["status"] = "missing_curl_and_codex"
            return
        installer = output / "official-codex-install.sh"
        print("未找到 Codex，下载并运行官方安装器（无需 Node.js）。", flush=True)
        rc = subprocess.run([curl, "-fSL", "--connect-timeout", "15", "--max-time", "120",
                             "https://chatgpt.com/codex/install.sh", "-o", str(installer)]).returncode
        if rc:
            report.update(status="installer_download_failed", installer_download_exit_code=rc)
            return
        rc = subprocess.run(["sh", str(installer)], timeout=600).returncode
        report["installer_exit_code"] = rc
        cli = shutil.which("codex") or (str(fallback) if fallback.is_file() else None)
        if rc or not cli:
            report["status"] = "installation_failed_or_cli_not_found"
            return
    report["cli_found"] = bool(cli)
    if not cli:
        report["status"] = "probe_complete_cli_missing"
        return
    version = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20)
    match = re.search(r"codex-cli\s+[\w.+-]+", version.stdout)
    report["cli_version"] = match.group(0) if match else "unrecognized"
    if args.probe_only:
        report["status"] = "probe_complete_no_model_call"
        return
    auth = subprocess.run([cli, "login", "status"], capture_output=True, timeout=30)
    if auth.returncode:
        print("请在你自己的 Mac 浏览器中打开接下来显示的网址并输入设备码。不要分享设备码。", flush=True)
        # Deliberately inherit the terminal. Never capture or archive login output.
        rc = subprocess.run([cli, "login", "--device-auth"], timeout=600).returncode
        report["login_exit_code"] = rc
        if rc:
            report["status"] = "device_login_failed"
            return
    auth = subprocess.run([cli, "login", "status"], capture_output=True, timeout=30)
    report["login_status_exit_code"] = auth.returncode
    if auth.returncode:
        report["status"] = "login_not_confirmed"
        return
    print("开始一次极短模型请求，最多等待 180 秒。", flush=True)
    with tempfile.TemporaryDirectory(prefix="room-codex-probe-") as empty:
        cmd = [cli, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral"]
        help_result = subprocess.run([cli, "exec", "--help"], capture_output=True, text=True, timeout=20)
        if "--ignore-user-config" in help_result.stdout:
            cmd.append("--ignore-user-config")
        else:
            report["status"] = "cli_update_required_for_isolated_probe"
            return
        if args.model:
            cmd += ["--model", args.model]
        cmd += ["Do not use tools. Do not read files. Reply with exactly ROOM_CODEX_OK and nothing else."]
        result = subprocess.run(cmd, cwd=empty, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=180)
        report["model_call_exit_code"] = result.returncode
        report["expected_reply_received"] = result.stdout.strip() == "ROOM_CODEX_OK"
        report["error_categories"] = classify(result.stderr)
        report["status"] = ("model_call_passed" if result.returncode == 0 and
                            report["expected_reply_received"] else "model_call_failed_or_unexpected_reply")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/yinxin/projects/room")
    parser.add_argument("--probe-only", action="store_true", help="No install, login or model call")
    parser.add_argument("--skip-network-probe", action="store_true")
    parser.add_argument("--model", help="Optional explicit model; otherwise use Codex default")
    args = parser.parse_args()
    os.umask(0o077)
    name = "room_codex_check_" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:8]
    root = Path(args.root).absolute() / "return_output"
    output = root / "artifacts" / name
    output.mkdir(parents=True)
    report = {"status": "started", "requested_model": args.model or "CLI default",
              "full_litereality_tested": False, "credentials_included": False}
    try:
        run_check(args, report, output)
    except subprocess.TimeoutExpired:
        report["status"] = "command_timed_out"
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as exc:
        report.update(status="check_failed", error_type=type(exc).__name__)
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    (output / "report.json").write_bytes(payload)
    archive = root / (name + ".tar.gz")
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as tar:
                entry = tarfile.TarInfo(name + "/report.json")
                entry.size, entry.mtime, entry.mode = len(payload), 0, 0o600
                tar.addfile(entry, io.BytesIO(payload))
    print("RESULT: " + report["status"])
    print("RETURN ARCHIVE: " + str(archive))
    return 0 if report["status"] in {"model_call_passed", "probe_complete_no_model_call", "probe_complete_cli_missing"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
