# Packs: one arm, one job per pack

`agent/arm_server.py` owns the arm (motion, safety, vision). `agent/arm_mcp.py` turns its HTTP API into MCP tools;
`PACK` picks which tools and which instructions Claude gets.

```
.venv/bin/python agent/live_vision.py          # sorter needs it
.venv/bin/python agent/arm_server.py [--go]    # dry run without --go
claude mcp add feeder -e PACK=feeder -- $PWD/.venv/bin/python $PWD/agent/arm_mcp.py
claude mcp add sorter -e PACK=sorter -- $PWD/.venv/bin/python $PWD/agent/arm_mcp.py
```

New job: add an endpoint in `arm_server.py` (if a new motion is needed), its tool in `ALL` in `arm_mcp.py`, and a
folder here with `pack.json` (`tools`, `instructions`). The ElevenLabs voice page keeps working beside it.
