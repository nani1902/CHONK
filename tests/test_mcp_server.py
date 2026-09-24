"""Run the real MCP server over stdio and check the whole transcript for leaks."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pdfs
import pytest

mcp = pytest.importorskip("mcp")
anyio = pytest.importorskip("anyio")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

EXPECTED_TOOLS = {"select_document", "list_vault", "compress", "privacy_status"}


def test_server_end_to_end(tmp_path: Path, scanned: bytes):
    vault = tmp_path / "Private"
    vault.mkdir()
    (vault / f"passport_{pdfs.SECRET}.pdf").write_bytes(scanned)
    home = tmp_path / "home"
    home.mkdir()
    environment = {key: value for key, value in os.environ.items() if key not in ("DISPLAY", "WAYLAND_DISPLAY")}
    environment.update(HOME=str(home), CHONK_OCR="none")
    params = StdioServerParameters(command=sys.executable, args=["-m", "chonk", "mcp", "--vault", str(vault)],
                                   env=environment, cwd=str(tmp_path))
    transcript: list[str] = []
    stderr_path = tmp_path / "stderr.txt"

    async def session() -> None:
        with stderr_path.open("w") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as client:
                    transcript.append((await client.initialize()).model_dump_json())
                    tools = await client.list_tools()
                    transcript.append(tools.model_dump_json())
                    assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS
                    for tool in tools.tools:  # nothing that could return content
                        assert not any(word in tool.name for word in ("render", "crop", "text", "preview", "path"))

                    picked = await client.call_tool("select_document", {})
                    transcript.append(picked.model_dump_json())
                    assert picked.structured_content["status"] == "error:picker_unavailable"

                    listed = await client.call_tool("list_vault", {})
                    transcript.append(listed.model_dump_json())
                    doc = listed.structured_content["documents"][0]["doc"]

                    result = await client.call_tool("compress", {"doc": doc, "target": "400KB"})
                    transcript.append(result.model_dump_json())
                    content = result.structured_content
                    assert content["status"] == "fit" and content["bytes"] <= 400_000
                    assert content["user_review"] == "unavailable"  # headless
                    assert content["saved_to"] == "vault_outbox"

                    status = await client.call_tool("privacy_status", {})
                    transcript.append(status.model_dump_json())
                    assert status.structured_content["privacy_posture"] in ("open", "partial", "walled")

    anyio.run(session)
    blob = "\n".join(transcript)
    for needle in (pdfs.SECRET, "passport", str(vault), str(tmp_path)):
        assert needle not in blob
    assert stderr_path.read_text() == ""  # nothing on stderr either
    assert len(list((vault / "outbox").glob("*.pdf"))) == 1
    log = (vault / ".chonk" / "chonk.log")
    if os.name != "nt":
        assert log.stat().st_mode & 0o077 == 0
    json.loads(transcript[1])  # the tool list is valid JSON
