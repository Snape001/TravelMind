"""Bounded online collection by orchestrating the pinned MediaCrawler checkout.

The crawler runs in a visible browser and requires the user to scan the platform
QR code with their own account. The pinned checkout already bundles its own
anti-detection for standard Playwright runs; this module adds no captcha
solving, no proxy pool and keeps concurrency at 1. The launcher disables the
upstream `CDP_CONNECT_EXISTING` switch so the crawler starts its own browser
with a dedicated profile instead of waiting for a pre-opened browser on the
debug port. Any failure (unexpected checkout, crawler error, timeout, empty
output) aborts the batch with a distinct exit code and leaves
`runs/<batch>/crawler.log` for inspection.
"""

import argparse
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from travelmind.research.social.schemas import ImportRequest, Keyword

Mode = Literal["search", "detail"]
UPSTREAM_COMMIT = "306ab4ca26c0e44fb87ede0e40e98748752815a6"
PLATFORM_KEYS: dict[str, str] = {
    "xiaohongshu": "xhs",
    "bilibili": "bili",
    "douyin": "dy",
    "weibo": "wb",
    "zhihu": "zhihu",
}
DEFAULT_MAX_NOTES = 20
MAX_NOTES_LIMIT = 50
DEFAULT_TIMEOUT_SECONDS = 1800
MIN_TIMEOUT_SECONDS = 60
MAX_COMMENTS_PER_NOTE = 20
CHUNK_BYTES = 65536


class MediacrawlerError(Exception):
    """Collection failure mapped to a stable CLI exit code."""

    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def resolve_commit(source: Path) -> str | None:
    """Read the checked-out commit from a git directory without invoking git."""
    head = source / ".git" / "HEAD"
    if not head.is_file():
        return None
    text = head.read_text(encoding="utf-8").strip()
    if not text.startswith("ref: "):
        return text.lower() or None
    reference = source / ".git" / text[5:].strip()
    if reference.is_file():
        return reference.read_text(encoding="utf-8").strip().lower() or None
    packed = source / ".git" / "packed-refs"
    if packed.is_file():
        name = text[5:].strip()
        for line in packed.read_text(encoding="utf-8").splitlines():
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == name:
                return parts[0].strip().lower()
    return None


def verify_source(source: Path) -> Path:
    """Return the upstream venv interpreter after checking the pinned commit."""
    if resolve_commit(source) != UPSTREAM_COMMIT:
        raise MediacrawlerError(3, "UNEXPECTED_CHECKOUT")
    python = source / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise MediacrawlerError(3, "MISSING_VENV")
    return python


CDP_BOOTSTRAP = (
    "import runpy\n"
    "import config\n"
    "config.CDP_CONNECT_EXISTING = False\n"
    "runpy.run_path('main.py', run_name='__main__')\n"
)


def build_command(
    python: Path,
    platform_key: str,
    keyword: str,
    mode: Mode,
    url: str | None,
    max_notes: int,
    save_path: Path,
) -> list[str]:
    command = [
        str(python),
        "-c",
        CDP_BOOTSTRAP,
        "--platform",
        platform_key,
        "--lt",
        "qrcode",
        "--type",
        mode,
        "--keywords",
        keyword,
        "--get_comment",
        "true",
        "--get_sub_comment",
        "true",
        "--get_media",
        "false",
        "--headless",
        "false",
        "--save_data_option",
        "jsonl",
        "--save_data_path",
        str(save_path),
        "--crawler_max_notes_count",
        str(max_notes),
        "--max_comments_count_singlenotes",
        str(MAX_COMMENTS_PER_NOTE),
        "--max_concurrency_num",
        "1",
        "--enable_ip_proxy",
        "false",
    ]
    if mode == "detail":
        command += ["--specified_id", str(url)]
    return command


def _child_env() -> dict[str, str]:
    """Copy the environment after dropping bracketed IPv6 entries from NO_PROXY.

    Some networks export `[::1]`-style entries; httpx rejects them as an invalid
    port when it checks the local debug endpoint, which breaks the CDP handshake.
    """
    env = os.environ.copy()
    for name in ("NO_PROXY", "no_proxy"):
        raw = env.get(name)
        if not raw:
            continue
        entries = [part.strip() for part in raw.split(",")]
        env[name] = ",".join(part for part in entries if part and "[" not in part)
    return env


def run_crawler(
    command: list[str],
    source: Path,
    log_path: Path,
    timeout_seconds: int,
    runner=None,
) -> None:
    if runner is None:
        runner = subprocess.run
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        try:
            result = runner(
                command,
                cwd=str(source),
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired:
            raise MediacrawlerError(5, "CRAWLER_TIMEOUT") from None
    if result.returncode != 0:
        raise MediacrawlerError(4, f"CRAWLER_FAILED exit={result.returncode}")


def _concat(sources: list[Path], target: Path) -> None:
    with target.open("wb") as output:
        for path in sources:
            with path.open("rb") as stream:
                while chunk := stream.read(CHUNK_BYTES):
                    output.write(chunk)


def stage_outputs(run_dir: Path, batch: Path, platform_key: str, mode: Mode) -> None:
    """Concatenate the crawler's dated JSONL exports into one import batch."""
    export = run_dir / platform_key / "jsonl"
    contents = sorted(export.glob(f"{mode}_contents_*.jsonl"))
    if not contents:
        raise MediacrawlerError(6, "NO_CONTENTS")
    batch.mkdir(parents=True, exist_ok=False)
    _concat(contents, batch / "contents.jsonl")
    comments = sorted(export.glob(f"{mode}_comments_*.jsonl"))
    if comments:
        _concat(comments, batch / "comments.jsonl")


def collect(
    *,
    source: Path,
    data: Path,
    platform: str,
    keyword: str,
    mode: Mode,
    url: str | None,
    max_notes: int,
    timeout_seconds: int,
    runner=None,
) -> ImportRequest:
    """Run one bounded crawl and stage its output as a new import batch."""
    if platform not in PLATFORM_KEYS:
        raise MediacrawlerError(2, "INVALID_PLATFORM")
    python = verify_source(source)
    batch_id = uuid4()
    run_dir = data / "runs" / str(batch_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    command = build_command(python, PLATFORM_KEYS[platform], keyword, mode, url, max_notes, run_dir)
    run_crawler(command, source, run_dir / "crawler.log", timeout_seconds, runner=runner)
    collected = datetime.now(UTC)
    request = ImportRequest(
        batch_id=batch_id,
        platform=platform,
        keyword=keyword,
        collected_at=collected,
        format="mediacrawler",
    )
    stage_outputs(run_dir, data / "inbox" / str(batch_id), PLATFORM_KEYS[platform], mode)
    return request


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="sidecar data directory")
    parser.add_argument("--source", type=Path, required=True, help="pinned MediaCrawler checkout")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--platform", choices=tuple(PLATFORM_KEYS), required=True)
    parser.add_argument("--mode", choices=("search", "detail"), default="search")
    parser.add_argument("--url", help="content URL for detail mode")
    parser.add_argument("--max-notes", type=int, default=DEFAULT_MAX_NOTES)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        keyword = TypeAdapter(Keyword).validate_python(args.keyword)
    except ValidationError:
        parser.exit(2, "INVALID_KEYWORD\n")
    if args.mode == "detail" and not args.url:
        parser.error("--url is required in detail mode")
    if not 1 <= args.max_notes <= MAX_NOTES_LIMIT:
        parser.error(f"--max-notes must be between 1 and {MAX_NOTES_LIMIT}")
    if not MIN_TIMEOUT_SECONDS <= args.timeout_seconds:
        parser.error(f"--timeout-seconds must be at least {MIN_TIMEOUT_SECONDS}")
    try:
        request = collect(
            source=args.source,
            data=args.data,
            platform=args.platform,
            keyword=keyword,
            mode=args.mode,
            url=args.url,
            max_notes=args.max_notes,
            timeout_seconds=args.timeout_seconds,
        )
    except MediacrawlerError as exc:
        parser.exit(exc.exit_code, f"MEDIACRAWLER {exc.message}\n")
    print(json.dumps(request.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
