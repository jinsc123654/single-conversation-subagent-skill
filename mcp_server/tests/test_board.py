"""Local Markdown board tests (storage + MCP tool wrapper)."""

import json
from pathlib import Path

import pytest
from mcp import Client

from scs_mcp.board import Board, BoardError
from scs_mcp.server import create_server


def test_board_roundtrip_and_markdown_files(tmp_path):
    board = Board(tmp_path / "board")
    task = board.add_task("/tmp/demo", "实现登录", "细节说明", "high", "2026-11-30", "sess-1", "gui")
    assert task["state"] == "todo"
    assert task["session_id"] == "sess-1"
    path = tmp_path / "board/tasks" / f"{task['id']}.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8").startswith("---\n")
    assert json.loads((tmp_path / "board/events" / f"{task['id']}.jsonl").read_text().splitlines()[0])["type"] == "add"

    listed = board.list_tasks("/tmp/demo")
    assert listed["available"] is True
    assert listed["counts"]["todo"] == 1
    assert listed["tasks"][0]["id"] == task["id"]

    claimed = board.claim_task(task["id"], session_id="sess-1", owner="zcode")
    assert claimed["state"] == "claimed"
    started = board.start_task(task["id"], session_id="sess-1", owner="zcode")
    assert started["state"] == "in_progress"
    done = board.complete_task(task["id"], "zcode", summary="done")
    assert done["state"] == "done"
    assert [event["type"] for event in board.get_task(task["id"])["events"]] == ["add", "claim", "start", "complete"]


def test_board_claim_conflict_and_session_rules(tmp_path):
    board = Board(tmp_path / "board")
    task = board.add_task("/tmp/demo", "任务", session_id="sess-1")
    board.claim_task(task["id"], session_id="sess-1", owner="a")
    with pytest.raises(BoardError) as caught:
        board.claim_task(task["id"], session_id="sess-2", owner="b")
    assert caught.value.code == "TASK_CLAIMED"
    assert "board_task_add" in caught.value.details["hint"]

    other = board.add_task("/tmp/demo", "另一个")
    with pytest.raises(BoardError) as caught:
        board.start_task(other["id"], session_id="")
    assert caught.value.code == "SESSION_REQUIRED"

    mismatch = board.add_task("/tmp/demo", "绑定", session_id="sess-1")
    with pytest.raises(BoardError) as caught:
        board.claim_task(mismatch["id"], session_id="sess-2")
    assert caught.value.code == "TASK_SESSION_MISMATCH"


def test_board_update_session_and_abandon(tmp_path):
    board = Board(tmp_path / "board")
    task = board.add_task("/tmp/demo", "任务", session_id="sess-1")
    board.claim_task(task["id"], session_id="sess-1")
    changed = board.update_task(task["id"], "gui", title="新标题", session_id="sess-2")
    assert changed["title"] == "新标题"
    assert changed["created_session"] == "sess-2"
    assert changed["session_id"] == "sess-2"
    abandoned = board.abandon_task(task["id"], "gui", "不做了")
    assert abandoned["state"] == "abandoned"
    with pytest.raises(BoardError) as caught:
        board.complete_task(task["id"], "gui")
    assert caught.value.code == "TASK_CLOSED"


def test_board_prompt_contains_flow_and_fallback(tmp_path):
    board = Board(tmp_path / "board")
    task = board.add_task("/tmp/demo", "任务", session_id="sess-1")
    prompt = board.task_prompt(task["id"])
    assert task["id"] in prompt
    assert "board_task_claim" in prompt
    assert "board_task_start" in prompt
    assert "board_task_complete" in prompt
    assert "board_task_add" in prompt
    assert "TASK_CLAIMED" in prompt


def test_board_corrupt_file_is_reported_not_fatal(tmp_path):
    board = Board(tmp_path / "board")
    task = board.add_task("/tmp/demo", "正常")
    (tmp_path / "board/tasks/broken.md").write_text("not frontmatter", encoding="utf-8")
    listed = board.list_tasks("/tmp/demo")
    assert [item["id"] for item in listed["tasks"]] == [task["id"]]
    assert any("broken.md" in error for error in listed["errors"])


async def test_board_tools_via_in_process_client(tmp_path):
    board_dir = tmp_path / "board"
    async with Client(create_server(tmp_path, board_dir=board_dir)) as client:
        created = (await client.call_tool("board_task_add", {
            "title": "工具任务", "project_root": str(tmp_path), "session_id": "sess-tool",
            "detail": "从 MCP 创建", "priority": "high"})).structured_content
        task_id = created["task"]["id"]
        assert created["task"]["state"] == "todo"

        listed = (await client.call_tool("board_task_list", {"project_root": str(tmp_path)})).structured_content
        assert [item["id"] for item in listed["tasks"]] == [task_id]

        claimed = (await client.call_tool("board_task_claim", {
            "task_id": task_id, "session_id": "sess-tool", "owner": "zcode"})).structured_content
        assert claimed["task"]["state"] == "claimed"

        conflict = (await client.call_tool("board_task_claim", {
            "task_id": task_id, "session_id": "sess-other", "owner": "other"})).structured_content
        assert conflict["status"] == "ERROR"
        assert conflict["error_code"] == "TASK_CLAIMED"
        assert "board_task_add" in conflict["details"]["hint"]

        started = (await client.call_tool("board_task_start", {
            "task_id": task_id, "session_id": "sess-tool"})).structured_content
        assert started["task"]["state"] == "in_progress"
        completed = (await client.call_tool("board_task_complete", {
            "task_id": task_id, "actor": "zcode", "summary": "ok"})).structured_content
        assert completed["task"]["state"] == "done"

        prompt = (await client.call_tool("board_task_prompt", {"task_id": task_id})).structured_content
        assert task_id in prompt["prompt"]
