# External wallet artifact locator

This directory is a lightweight locator, not a second copy of the wallet
history. The immutable physical snapshot is:

```text
/Volumes/jrs-archive/pm_agents/research/external_wallet_weather/raw/
wallet=0x43cb4ae1f4ddc9e671486c79c9f40a6fd98b84df/
snapshot=20260729T121249Z
```

`manifest.json` has SHA-256
`31e6f7e04ff37a0345385237b391068151b1f911b3d061994b39fd71c1240f96`.
The archive copy and the former NVMe compatibility copy have identical file
inventories (3,557 files), source-manifest SHA and import-manifest SHA.

Important relative artifacts:

- `manifest.json`
- `weather_activity.jsonl.gz`
- `event_metadata.jsonl.gz`
- `analysis/full_ladder_history_v1/summary.json`
- `analysis/full_ladder_history_v1/event_portfolios.csv`
- `analysis/full_ladder_history_v1/monthly_summary.csv`
- `analysis/full_ladder_history_v1/city_summary.csv`

Local absolute symlinks may be created for interactive inspection, but they
are intentionally ignored by Git and are never a portable data contract.
