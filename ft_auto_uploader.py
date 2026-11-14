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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload files placed in a watched folder.")
    parser.add_argument("--watch-dir", required=True, type=Path)
    parser.add_argument("--stable-seconds", type=float, default=5.0)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--scan-timeout-minutes", type=float, default=30.0)
    parser.add_argument("--login-timeout-minutes", type=float, default=15.0)
    return parser.parse_args()


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

    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() in TEMP_SUFFIXES:
            continue
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
    if not response.ok:
        raise RuntimeError(f"Upload failed with HTTP {response.status}")
    scan_file_id = response.json().get("scanFileId")
    if not scan_file_id:
        raise RuntimeError("Upload response did not contain scanFileId")
    return scan_file_id


def wait_for_scan(request_context, scan_file_id: str, timeout_seconds: float) -> tuple[str, dict | None]:
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
    response = request_context.post(
        QUEUE_URL,
        data={"groupId": group_id, "scanFileId": scan_file_id},
    )
    if not response.ok or response.json().get("isSuccess") is not True:
        raise RuntimeError(f"Queue request failed with HTTP {response.status}")


def process_file(connection, context, page, path: Path, archive_dir: Path, scan_timeout: float) -> None:
    file_hash = fingerprint(path)
    row = connection.execute(
        "SELECT scan_file_id, status FROM uploads WHERE fingerprint = ?", (file_hash,)
    ).fetchone()

    if row and row[1] == "queued":
        archived = archive_file(path, archive_dir)
        print(f"قبلاً ارسال شده؛ بایگانی شد: {archived.name}")
        return
    if row and row[1] in {"rejected", "unknown"}:
        return

    scan_file_id = row[0] if row else None
    if not scan_file_id:
        scan_file_id = upload_file(page, path)
        save_state(connection, file_hash, path, scan_file_id, "uploaded")
        print(f"آپلود شد و در انتظار اسکن است: {path.name}")

    result, item = wait_for_scan(context.request, scan_file_id, scan_timeout)
    if result == "clean":
        queue_clean_file(context.request, item)
        save_state(connection, file_hash, path, scan_file_id, "queued")
        archived = archive_file(path, archive_dir)
        print(f"ارسال و بایگانی شد: {archived.name}")
    elif result == "queued":
        save_state(connection, file_hash, path, scan_file_id, "queued")
        archived = archive_file(path, archive_dir)
        print(f"ارسال قبلی تأیید و بایگانی شد: {archived.name}")
    elif result in {"rejected", "unknown"}:
        save_state(connection, file_hash, path, scan_file_id, result)
        print(f"به‌دلیل نتیجه اسکن «{result}» منتقل نشد: {path.name}")
    else:
        print(f"مهلت انتظار اسکن تمام شد؛ در اجرای بعد ادامه می‌یابد: {path.name}")


def main() -> int:
    args = parse_args()
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
    with sync_playwright() as playwright:
        context, page = launch_persistent_session(
            playwright, app_data / "browser-profile"
        )
        wait_for_file_manager(page, int(args.login_timeout_minutes * 60_000))
        print(f"پایش پوشه آغاز شد: {watch_dir}")

        try:
            while True:
                for path in find_ready_files(
                    watch_dir, observations, args.stable_seconds
                ):
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
                    except Exception as error:
                        print(f"خطا برای {path.name}: {error}")
                time.sleep(args.poll_seconds)
        except KeyboardInterrupt:
            print("پایش متوقف شد.")
        finally:
            context.close()
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
