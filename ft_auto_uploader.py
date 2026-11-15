"""Watch a folder and upload stable files through an authenticated FT browser session.

First run:
    pip install -r requirements.txt
    python -m playwright install chromium
    python ft_auto_uploader.py --watch-dir "C:\\FT-Inbox"

The browser profile is kept under LOCALAPPDATA.  Login and CAPTCHA entry remain
manual; no username or password is stored by this program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path

from ft_browser import app_data_dir, launch_persistent_session


BASE_URL = "https://ft.nioc.ir/"
QUARANTINE_URL = BASE_URL + "sarp-api/api/Scan/GetAllQuarantinedFilesList"
QUEUE_URL = BASE_URL + "sarp-api/api/Scan/QueueForUploadFileFromQuarantineToSaman"
UPLOAD_RESPONSE_SUFFIX = "/sarp-api/api/Scan/UploadFile"
TEMP_SUFFIXES = {".crdownload", ".part", ".tmp"}
NETWORK_DEBUG_NAME = "last-upload-network.console.txt"


def _network_debug_path() -> Path:
    return app_data_dir() / NETWORK_DEBUG_NAME


def _write_network_debug(text: str, *, reset: bool = False) -> None:
    """Best-effort diagnostic output; it must never affect an upload."""
    try:
        path = _network_debug_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w" if reset else "a", encoding="utf-8", newline="\n") as stream:
            stream.write(text.rstrip() + "\n\n")
    except OSError as error:
        print(f"هشدار: فایل دستورات شبکه نوشته نشد: {error}")


def _response_logging_lines() -> list[str]:
    return [
        "  const responseText = await response.text();",
        "  console.log('HTTP status:', response.status);",
        "  console.log('Response body:', responseText);",
    ]


def _build_upload_console_script(url: str, field_name: str = "file") -> str:
    """Build a replayable upload without persisting cookies or file bytes."""
    selector = "#main-page-blank > input"
    return "\n".join(
        [
            "// Upload: run while signed in to ft.nioc.ir.",
            "(async () => {",
            f"  const existingInput = document.querySelector({json.dumps(selector)});",
            "  let file = existingInput?.files?.[0];",
            "  if (!file) {",
            "    file = await new Promise((resolve, reject) => {",
            "      const picker = document.createElement('input');",
            "      picker.type = 'file';",
            "      picker.onchange = () => picker.files?.[0] ? resolve(picker.files[0]) : reject(new Error('No file selected'));",
            "      picker.click();",
            "    });",
            "  }",
            "  const body = new FormData();",
            f"  body.append({json.dumps(field_name)}, file, file.name);",
            f"  const response = await fetch({json.dumps(url)}, {{",
            "    method: 'POST',",
            "    credentials: 'include',",
            "    headers: {'accept': 'application/json, text/plain, */*'},",
            "    body,",
            "  });",
            *_response_logging_lines(),
            "})().catch((error) => console.error('Request failed:', error));",
        ]
    )


def _build_api_console_script(
    title: str, url: str, method: str, payload: dict | None = None
) -> str:
    headers = "{'accept': 'application/json, text/plain, */*'"
    headers += ", 'content-type': 'application/json'}" if payload is not None else "}"
    lines = [
        f"// {title}: run while signed in to ft.nioc.ir.",
        "(async () => {",
        f"  const response = await fetch({json.dumps(url)}, {{",
        f"    method: {json.dumps(method)},",
        "    credentials: 'include',",
        f"    headers: {headers},",
    ]
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"    body: JSON.stringify({body}),")
    lines.extend(
        [
            "  });",
            *_response_logging_lines(),
            "})().catch((error) => console.error('Request failed:', error));",
        ]
    )
    return "\n".join(lines)


def _multipart_file_field(request) -> str:
    try:
        body = request.post_data_buffer or b""
        match = re.search(
            br'content-disposition:\s*form-data;\s*name="([^"]+)";\s*filename=',
            body,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).decode("utf-8", errors="replace")
    except Exception:
        pass
    return "file"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload files placed in a watched folder.")
    parser.add_argument("--watch-dir", required=True, type=Path)
    parser.add_argument("--stable-seconds", type=float, default=5.0)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--scan-timeout-minutes", type=float, default=30.0)
    parser.add_argument("--login-timeout-minutes", type=float, default=15.0)
    parser.add_argument(
        "--max-files-per-run",
        type=int,
        default=0,
        help="Maximum files per cycle; 0 means unlimited.",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=float,
        default=0.0,
        help="Maximum size of each file in MiB; 0 means unlimited.",
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Process one stable-file cycle and exit.",
    )
    return parser.parse_args()


def list_candidate_files(folder: Path) -> list[Path]:
    return [
        path
        for path in sorted(Path(folder).iterdir())
        if path.is_file() and path.suffix.lower() not in TEMP_SUFFIXES
    ]


def find_ready_files(
    folder: Path,
    observations: dict[Path, tuple[int, int, float]],
    stable_seconds: float,
    now: float | None = None,
) -> list[Path]:
    """Return files whose size and modification time stayed unchanged long enough."""
    now = time.monotonic() if now is None else now
    current_paths = set()
    ready = []

    for path in list_candidate_files(folder):
        current_paths.add(path)
        stat = path.stat()
        previous = observations.get(path)
        signature = (stat.st_size, stat.st_mtime_ns)

        if previous and previous[:2] == signature:
            if now - previous[2] >= stable_seconds:
                ready.append(path)
        else:
            observations[path] = (*signature, now)

    for missing in set(observations) - current_paths:
        observations.pop(missing, None)
    return ready


def select_files_for_run(
    paths: list[Path], max_files: int = 0, max_size_bytes: int = 0
) -> tuple[list[Path], list[Path]]:
    """Apply optional count and size limits while preserving path order."""
    if max_files < 0 or max_size_bytes < 0:
        raise ValueError("محدودیت تعداد و حجم نباید منفی باشد.")
    oversized = [path for path in paths if max_size_bytes and path.stat().st_size > max_size_bytes]
    accepted = [path for path in paths if path not in oversized]
    if max_files:
        accepted = accepted[:max_files]
    return accepted, oversized


def _report(emit, level: str, message: str) -> None:
    if emit is None:
        print(message)
    else:
        emit(level, message)


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_state(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS uploads (
            fingerprint TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            scan_file_id TEXT,
            status TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    return connection


def save_state(
    connection: sqlite3.Connection,
    file_hash: str,
    source_path: Path,
    scan_file_id: str | None,
    status: str,
) -> None:
    connection.execute(
        """
        INSERT INTO uploads (fingerprint, source_path, scan_file_id, status, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            source_path=excluded.source_path,
            scan_file_id=excluded.scan_file_id,
            status=excluded.status,
            updated_at=excluded.updated_at
        """,
        (file_hash, str(source_path), scan_file_id, status, int(time.time())),
    )
    connection.commit()


def list_upload_history(connection: sqlite3.Connection) -> list[tuple[str, str, int]]:
    return connection.execute(
        """
        SELECT source_path, status, updated_at
        FROM uploads
        ORDER BY updated_at DESC, source_path
        """
    ).fetchall()


def scan_result(item: dict) -> str:
    """Fail closed: only explicit clean results are accepted."""
    if item.get("fileScanState") == "SendingToSaman" or item.get("isEnqueued") is True:
        return "queued"

    antivirus_results = item.get("avResults") or []
    if (
        item.get("isRejected") is True
        or item.get("isInfected") is True
        or any(result.get("wasInfected") is True for result in antivirus_results)
    ):
        return "rejected"

    if item.get("isFinished") is not True or item.get("fileScanState") != "Finished":
        return "pending"

    explicitly_clean = (
        item.get("isInfected") is False
        and item.get("isRejected") is False
        and bool(antivirus_results)
        and all(result.get("wasInfected") is False for result in antivirus_results)
    )
    return "clean" if explicitly_clean else "unknown"


def archive_file(path: Path, archive_dir: Path) -> Path:
    archive_dir.mkdir(exist_ok=True)
    destination = archive_dir / path.name
    counter = 1
    while destination.exists():
        destination = archive_dir / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    return Path(shutil.move(str(path), str(destination)))


def wait_for_manual_login(upload_button, login_timeout_ms: int) -> None:
    print("ورود لازم است؛ لطفاً در مرورگر وارد شوید و CAPTCHA را دستی وارد کنید.")
    upload_button.wait_for(state="visible", timeout=login_timeout_ms)


def wait_for_file_manager(page, login_timeout_ms: int, login_handler=None) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    upload_button = page.locator("button.mr-5").first
    try:
        upload_button.wait_for(state="visible", timeout=10_000)
    except Exception:
        handler = login_handler or wait_for_manual_login
        handler(upload_button, login_timeout_ms)


def upload_file(page, path: Path) -> str:
    page.locator("button.mr-5").first.click()
    file_input = page.locator("#main-page-blank > input")
    file_input.wait_for(state="attached", timeout=10_000)
    file_input.set_input_files(str(path))

    with page.expect_response(
        lambda response: response.url.endswith(UPLOAD_RESPONSE_SUFFIX)
        and response.request.method == "POST",
        timeout=120_000,
    ) as response_info:
        page.locator("div.v-card__actions button").nth(1).click()

    response = response_info.value
    request = response.request
    _write_network_debug(
        _build_upload_console_script(request.url, _multipart_file_field(request))
    )
    if not response.ok:
        raise RuntimeError(f"Upload failed with HTTP {response.status}")
    scan_file_id = response.json().get("scanFileId")
    if not scan_file_id:
        raise RuntimeError("Upload response did not contain scanFileId")
    return scan_file_id


def wait_for_scan(request_context, scan_file_id: str, timeout_seconds: float) -> tuple[str, dict | None]:
    _write_network_debug(_build_api_console_script("Scan status", QUARANTINE_URL, "GET"))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = request_context.get(QUARANTINE_URL)
        if not response.ok:
            raise RuntimeError(f"Scan status failed with HTTP {response.status}")
        item = next(
            (candidate for candidate in response.json().get("items", []) if candidate.get("id") == scan_file_id),
            None,
        )
        if item:
            result = scan_result(item)
            if result != "pending":
                return result, item
        time.sleep(3)
    return "timeout", None


def queue_clean_file(request_context, item: dict) -> None:
    group_id = item.get("groupId")
    scan_file_id = item.get("id")
    if not group_id or not scan_file_id:
        raise RuntimeError("Scan result is missing groupId or id")
    _write_network_debug(
        _build_api_console_script(
            "Queue clean file",
            QUEUE_URL,
            "POST",
            {"groupId": group_id, "scanFileId": scan_file_id},
        )
    )
    response = request_context.post(
        QUEUE_URL,
        data={"groupId": group_id, "scanFileId": scan_file_id},
    )
    if not response.ok or response.json().get("isSuccess") is not True:
        raise RuntimeError(f"Queue request failed with HTTP {response.status}")


def process_file(
    connection,
    context,
    page,
    path: Path,
    archive_dir: Path,
    scan_timeout: float,
    emit=None,
) -> str:
    _write_network_debug(
        "// Network commands for the latest FT upload.\n"
        "// Paste one JavaScript block at a time into the browser DevTools Console.",
        reset=True,
    )
    file_hash = fingerprint(path)
    row = connection.execute(
        "SELECT scan_file_id, status FROM uploads WHERE fingerprint = ?", (file_hash,)
    ).fetchone()

    if row and row[1] == "queued":
        archived = archive_file(path, archive_dir)
        _report(emit, "success", f"قبلاً ارسال شده؛ بایگانی شد: {archived.name}")
        return "queued"
    if row and row[1] in {"rejected", "unknown"}:
        _report(emit, "warning", f"نتیجه قبلی «{row[1]}» است؛ رد شد: {path.name}")
        return row[1]

    scan_file_id = row[0] if row else None
    if not scan_file_id:
        scan_file_id = upload_file(page, path)
        save_state(connection, file_hash, path, scan_file_id, "uploaded")
        _report(emit, "info", f"آپلود شد و در انتظار اسکن است: {path.name}")

    result, item = wait_for_scan(context.request, scan_file_id, scan_timeout)
    if result == "clean":
        queue_clean_file(context.request, item)
        save_state(connection, file_hash, path, scan_file_id, "queued")
        archived = archive_file(path, archive_dir)
        _report(emit, "success", f"ارسال و بایگانی شد: {archived.name}")
    elif result == "queued":
        save_state(connection, file_hash, path, scan_file_id, "queued")
        archived = archive_file(path, archive_dir)
        _report(emit, "success", f"ارسال قبلی تأیید و بایگانی شد: {archived.name}")
    elif result in {"rejected", "unknown"}:
        save_state(connection, file_hash, path, scan_file_id, result)
        _report(emit, "error", f"به‌دلیل نتیجه اسکن «{result}» منتقل نشد: {path.name}")
    else:
        _report(emit, "warning", f"مهلت انتظار اسکن تمام شد؛ در اجرای بعد ادامه می‌یابد: {path.name}")
    return result


def main() -> int:
    args = parse_args()
    if args.max_files_per_run < 0 or args.max_file_size_mb < 0:
        raise SystemExit("محدودیت تعداد و حجم نباید منفی باشد.")
    watch_dir = args.watch_dir.resolve()
    watch_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = watch_dir / "_uploaded"

    app_data = app_data_dir()
    app_data.mkdir(parents=True, exist_ok=True)
    connection = open_state(app_data / "state.db")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright نصب نیست؛ ابتدا pip install -r requirements.txt را اجرا کنید.")
        return 2

    observations: dict[Path, tuple[int, int, float]] = {}
    oversized_reported: dict[Path, tuple[int, int]] = {}
    with sync_playwright() as playwright:
        context, page = launch_persistent_session(
            playwright, app_data / "browser-profile"
        )
        wait_for_file_manager(page, int(args.login_timeout_minutes * 60_000))
        print(f"پایش پوشه آغاز شد: {watch_dir}")

        try:
            cycle_started = time.monotonic()
            consecutive_failures = 0
            stop_after_errors = False
            while True:
                ready = find_ready_files(watch_dir, observations, args.stable_seconds)
                selected, oversized = select_files_for_run(
                    ready,
                    args.max_files_per_run,
                    int(args.max_file_size_mb * 1024 * 1024),
                )
                for path in oversized:
                    stat = path.stat()
                    signature = (stat.st_size, stat.st_mtime_ns)
                    if oversized_reported.get(path) != signature:
                        print(f"به‌دلیل عبور از سقف حجم رد شد: {path.name}")
                        oversized_reported[path] = signature
                for missing in set(oversized_reported) - set(ready):
                    oversized_reported.pop(missing, None)
                for path in selected:
                    try:
                        process_file(
                            connection,
                            context,
                            page,
                            path,
                            archive_dir,
                            args.scan_timeout_minutes * 60,
                        )
                        observations.pop(path, None)
                        consecutive_failures = 0
                    except Exception as error:
                        consecutive_failures += 1
                        print(f"خطا برای {path.name}: {error}")
                        if consecutive_failures == 2:
                            print("دو ردیف پیاپی خطا دادند؛ برنامه خارج می‌شود.")
                            stop_after_errors = True
                            break
                if stop_after_errors:
                    break
                if args.run_once and time.monotonic() - cycle_started >= args.stable_seconds:
                    break
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("پایش متوقف شد.")
        finally:
            context.close()
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
