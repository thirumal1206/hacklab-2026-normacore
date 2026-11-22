- `agent/arm_mcp.py`: stdio MCP over arm_server.py's HTTP API; `PACK=feeder|sorter` picks the tools and instructions from
  `operator/packs/<pack>/pack.json`. No motion code in the MCP, so arm_server changes reach every pack. Dry run 22.11
  without Station: tool lists differ per pack, stop/feed/wait/home reach arm_server, feed stops at the IK call with
  "station_disconnected" (expected). Not verified on the arm.
