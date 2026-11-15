import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from travelmind.research.social import mediacrawler_api as mc
from travelmind.research.social.mediacrawler_api import (
    UPSTREAM_COMMIT,
    MediacrawlerError,
    build_command,
    collect,
    main,
    stage_outputs,
    verify_source,
)
from travelmind.research.social.platforms import load_export

KEYWORD = "泉州 预约"


def fake_source(tmp_path: Path, commit: str = UPSTREAM_COMMIT) -> Path:
    source = tmp_path / "MediaCrawler"
    git = source / ".git"
    git.mkdir(parents=True)
    (git / "HEAD").write_text(f"{commit}\n", encoding="utf-8")
    python = source / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    return source


def fake_runner(result=0, writer=None):
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        if isinstance(result, BaseException):
            raise result
        if writer:
            writer(command)
        return subprocess.CompletedProcess(command, result)

    run.calls = calls
    return run


def write_xhs_export(save_path: str) -> None:
    export = Path(save_path) / "xhs" / "jsonl"
    export.mkdir(parents=True)
    (export / "search_contents_2026-11-15.jsonl").write_text(
        json.dumps(
            {
                "note_id": "abc123",
                "desc": "蟳埔村簪花体验",
                "time": 1730000000,
                "comment_count": "2",
                "liked_count": "10",
                "source_keyword": KEYWORD,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (export / "search_contents_2026-11-16.jsonl").write_text(
        json.dumps(
            {
                "note_id": "def456",
                "desc": "泉州古城一日",
                "time": 1730100000,
                "comment_count": "1",
                "liked_count": "3",
                "source_keyword": KEYWORD,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (export / "search_comments_2026-11-15.jsonl").write_text(
        json.dumps(
            {
                "comment_id": "1",
                "note_id": "abc123",
                "content": "很美",
                "create_time": 1730000100,
                "like_count": 1,
                "parent_comment_id": "",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_verify_source_accepts_loose_and_packed_checkout_refs(tmp_path):
    source = fake_source(tmp_path)
    git = source / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    ref = git / "refs" / "heads" / "main"
    ref.write_text(f"{UPSTREAM_COMMIT}\n", encoding="utf-8")
    python = source / ".venv" / "Scripts" / "python.exe"
    assert verify_source(source) == python
    ref.unlink()
    (git / "packed-refs").write_text(f"{UPSTREAM_COMMIT} refs/heads/main\n", encoding="utf-8")
    assert verify_source(source) == python


def test_verify_source_rejects_missing_checkout_wrong_commit_and_missing_venv(tmp_path):
    with pytest.raises(MediacrawlerError) as absent:
        verify_source(tmp_path / "missing")
    assert (absent.value.exit_code, absent.value.message) == (3, "UNEXPECTED_CHECKOUT")

    source = fake_source(tmp_path, commit="0" * 40)
    with pytest.raises(MediacrawlerError) as wrong:
        verify_source(source)
    assert wrong.value.exit_code == 3 and wrong.value.message == "UNEXPECTED_CHECKOUT"
    source = fake_source(tmp_path / "without-venv")
    (source / ".venv" / "Scripts" / "python.exe").unlink()
    with pytest.raises(MediacrawlerError) as missing:
        verify_source(source)
    assert missing.value.exit_code == 3 and missing.value.message == "MISSING_VENV"


def test_build_command_detail_passes_source_url(tmp_path):
    command = build_command(
        tmp_path / "python.exe",
        "bili",
        KEYWORD,
        "detail",
        "https://www.bilibili.com/video/av1",
        5,
        tmp_path / "save",
    )
    pairs = dict(zip(command[3::2], command[4::2]))
    assert pairs["--type"] == "detail"
    assert pairs["--specified_id"] == "https://www.bilibili.com/video/av1"


def test_stage_outputs_concatenates_dated_exports_in_order(tmp_path):
    run_dir = tmp_path / "run"
    batch = tmp_path / "inbox" / "batch"
    export = run_dir / "xhs" / "jsonl"
    export.mkdir(parents=True)
    (export / "search_contents_2026-11-15.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    (export / "search_contents_2026-11-16.jsonl").write_text('{"b":2}\n', encoding="utf-8")
    stage_outputs(run_dir, batch, "xhs", "search")
    assert (batch / "contents.jsonl").read_text(encoding="utf-8") == '{"a":1}\n{"b":2}\n'
    assert not (batch / "comments.jsonl").exists()
    (export / "search_comments_2026-11-16.jsonl").write_text('{"c":3}\n', encoding="utf-8")
    batch2 = tmp_path / "inbox" / "batch2"
    stage_outputs(run_dir, batch2, "xhs", "search")
    assert (batch2 / "comments.jsonl").read_text(encoding="utf-8") == '{"c":3}\n'


def test_stage_outputs_backfills_blank_source_keyword(tmp_path):
    run_dir = tmp_path / "run"
    batch = tmp_path / "inbox" / "batch"
    export = run_dir / "zhihu" / "jsonl"
    export.mkdir(parents=True)
    (export / "detail_contents_2026-11-15.jsonl").write_text(
        '{"content_id":"1","source_keyword":"","title":"t"}\n'
        '{"content_id":"2","source_keyword":"keep","title":"u"}\n',
        encoding="utf-8",
    )
    stage_outputs(run_dir, batch, "zhihu", "detail", keyword=KEYWORD)
    rows = [
        json.loads(line)
        for line in (batch / "contents.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["source_keyword"] == KEYWORD
    assert rows[1]["source_keyword"] == "keep"


def test_collect_runs_a_bounded_search_and_imports_the_exports(tmp_path, monkeypatch):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "localhost,127.0.0.1,::1,[::1]")
    source = fake_source(tmp_path)
    data = tmp_path / "data"
    runner = fake_runner(
        writer=lambda command: write_xhs_export(
            dict(zip(command[3::2], command[4::2]))["--save_data_path"]
        )
    )
    before = datetime.now(UTC)
    request = collect(
        source=source,
        data=data,
        platform="xiaohongshu",
        keyword=KEYWORD,
        mode="search",
        url=None,
        max_notes=20,
        timeout_seconds=600,
        runner=runner,
    )
    assert request.format == "mediacrawler" and request.platform == "xiaohongshu"
    assert request.keyword == KEYWORD and before <= request.collected_at <= datetime.now(UTC)
    batch = data / "inbox" / str(request.batch_id)
    assert (batch / "contents.jsonl").is_file() and (batch / "comments.jsonl").is_file()
    run = data / "runs" / str(request.batch_id)
    assert (run / "crawler.log").is_file()
    [(command, options)] = runner.calls
    assert options["cwd"] == str(source) and options["check"] is False
    assert options["timeout"] == 600 and options["stderr"] == subprocess.STDOUT
    env = options["env"]
    assert (env.get("NO_PROXY") or env.get("no_proxy")) == "localhost,127.0.0.1,::1"
    python = source / ".venv" / "Scripts" / "python.exe"
    assert command[0] == str(python) and command[1] == "-c"
    assert "CDP_CONNECT_EXISTING = False" in command[2]
    assert "runpy.run_path('main.py'" in command[2]
    pairs = dict(zip(command[3::2], command[4::2]))
    assert pairs["--platform"] == "xhs" and pairs["--lt"] == "qrcode"
    assert pairs["--type"] == "search" and pairs["--keywords"] == KEYWORD
    assert pairs["--get_comment"] == pairs["--get_sub_comment"] == "true"
    assert pairs["--headless"] == "false" and pairs["--get_media"] == "false"
    assert pairs["--save_data_option"] == "jsonl" and pairs["--save_data_path"] == str(run)
    assert pairs["--crawler_max_notes_count"] == "20"
    assert pairs["--max_concurrency_num"] == "1" and pairs["--enable_ip_proxy"] == "false"
    assert "--specified_id" not in command
    records, errors = load_export(batch, request)
    assert errors == 0 and {record.post_id for record in records} == {"abc123", "def456"}
    post = next(record for record in records if record.post_id == "abc123")
    assert post.comments and post.comments[0].content == "很美"


def test_collect_maps_failures_to_exit_codes(tmp_path):
    source = fake_source(tmp_path)
    data = tmp_path / "data"

    with pytest.raises(MediacrawlerError) as failed:
        collect(
            source=source,
            data=data,
            platform="bilibili",
            keyword=KEYWORD,
            mode="search",
            url=None,
            max_notes=5,
            timeout_seconds=600,
            runner=fake_runner(result=1),
        )
    assert (failed.value.exit_code, failed.value.message) == (4, "CRAWLER_FAILED exit=1")

    with pytest.raises(MediacrawlerError) as timed_out:
        collect(
            source=source,
            data=data,
            platform="bilibili",
            keyword=KEYWORD,
            mode="search",
            url=None,
            max_notes=5,
            timeout_seconds=600,
            runner=fake_runner(result=subprocess.TimeoutExpired(cmd="x", timeout=600)),
        )
    assert (timed_out.value.exit_code, timed_out.value.message) == (5, "CRAWLER_TIMEOUT")

    with pytest.raises(MediacrawlerError) as empty:
        collect(
            source=source,
            data=data,
            platform="bilibili",
            keyword=KEYWORD,
            mode="search",
            url=None,
            max_notes=5,
            timeout_seconds=600,
            runner=fake_runner(),
        )
    assert (empty.value.exit_code, empty.value.message) == (6, "NO_CONTENTS")

    wrong = fake_source(tmp_path / "wrong", commit="f" * 40)
    with pytest.raises(MediacrawlerError) as checkout:
        collect(
            source=wrong,
            data=data,
            platform="bilibili",
            keyword=KEYWORD,
            mode="search",
            url=None,
            max_notes=5,
            timeout_seconds=600,
            runner=fake_runner(),
        )
    assert checkout.value.exit_code == 3


def test_main_rejects_invalid_keyword_and_requires_detail_url(tmp_path, capsys):
    source = fake_source(tmp_path)
    base = ["--data", str(tmp_path / "data"), "--source", str(source), "--platform", "xiaohongshu"]
    with pytest.raises(SystemExit) as keyword_error:
        main(base + ["--keyword", "  "])
    assert keyword_error.value.code == 2
    assert capsys.readouterr().err == "INVALID_KEYWORD\n"
    with pytest.raises(SystemExit) as detail_error:
        main(base + ["--keyword", KEYWORD, "--mode", "detail"])
    assert detail_error.value.code == 2
    with pytest.raises(SystemExit) as notes_error:
        main(base + ["--keyword", KEYWORD, "--max-notes", "0"])
    assert notes_error.value.code == 2


def test_main_prints_import_request(tmp_path, capsys, monkeypatch):
    source = fake_source(tmp_path)
    monkeypatch.setattr(
        mc,
        "subprocess",
        type(
            "S",
            (),
            {
                "run": staticmethod(
                    fake_runner(
                        writer=lambda command: write_xhs_export(
                            dict(zip(command[3::2], command[4::2]))["--save_data_path"]
                        )
                    )
                ),
                "STDOUT": subprocess.STDOUT,
                "TimeoutExpired": subprocess.TimeoutExpired,
            },
        ),
    )
    main(
        [
            "--data",
            str(tmp_path / "data"),
            "--source",
            str(source),
            "--platform",
            "xiaohongshu",
            "--keyword",
            KEYWORD,
        ]
    )
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["platform"] == "xiaohongshu" and receipt["format"] == "mediacrawler"
    assert receipt["keyword"] == KEYWORD and receipt["collected_at"].endswith("Z")
    assert (tmp_path / "data" / "inbox" / receipt["batch_id"] / "contents.jsonl").is_file()
