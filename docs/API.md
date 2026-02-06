# Polymarket Tool Service API

This document describes the HTTP API exposed by `scripts/python/server.py`.
本文档描述 `scripts/python/server.py` 暴露的 HTTP 接口。

## Base URL

- Default (local): `http://localhost:8000`
- 默认（本地）：`http://localhost:8000`

## Auth

If the environment variable `TOOL_API_KEY` is set, all requests must include:
如果设置了环境变量 `TOOL_API_KEY`，所有请求必须包含：

- Header: `X-API-Key: <your_key>`

If `TOOL_API_KEY` is not set, auth is not enforced.
如果未设置 `TOOL_API_KEY`，则不启用鉴权。

## Endpoints

### 1) Health

- `GET /health`

Response:
```json
{"ok": true}
```
中文说明：服务健康检查。

### 2) Events

- `GET /events`

Query parameters:
- `tradeable` (bool, default `false`)
- `limit` (int, optional)
- `offset` (int, default `0`)

Response:
- List of `SimpleEvent`
中文说明：获取事件列表；`tradeable=true` 时返回可交易事件。

### 3) Tradeable Events (explicit)

- `GET /events/tradeable`

Query parameters:
- `limit` (int, optional)
- `offset` (int, default `0`)

Response:
- List of `SimpleEvent`
中文说明：直接返回“可交易事件”列表（等价于 `/events?tradeable=true`）。

### 4) Markets

- `GET /markets`

Query parameters:
- `tradeable` (bool, default `false`)
- `limit` (int, optional)
- `offset` (int, default `0`)

Response:
- List of `SimpleMarket`
中文说明：获取市场列表；`tradeable=true` 时返回可交易市场。

### 5) Single Market by token_id

- `GET /market/{token_id}`

Path parameters:
- `token_id`: CLOB token id

Response:
- `SimpleMarket`
中文说明：通过 `token_id` 获取单个市场信息。

### 6) Order Book

- `GET /orderbook/{token_id}`

Path parameters:
- `token_id`: CLOB token id

Response:
- CLOB order book object
中文说明：获取订单簿数据（CLOB）。

### 7) Wallet Balance

- `GET /balance`

Response:
```json
{
  "address": "0x...",
  "usdc_balance": 123.45
}
```
中文说明：查询钱包地址与 USDC 余额。

### 8) Place Limit Order

- `POST /order`

Body:
```json
{
  "token_id": "string",
  "price": 0.45,
  "size": 10.0,
  "side": "BUY"
}
```

Notes:
- `price` must be between 0 and 1
- `side` must be `BUY` or `SELL`

Response:
- CLOB order response
中文说明：下限价单（BUY/SELL）。

### 9) Place Market Order (FOK)

- `POST /market-order`

Body:
```json
{
  "token_id": "string",
  "amount": 100.0
}
```

Notes:
- `amount` is in USDC

Response:
- CLOB order response
中文说明：下市价单（FOK）。

### 10) CTF Split (USDC -> YES/NO)

- `POST /ctf/split`

Body:
```json
{
  "condition_id": "0x...",
  "partition": [1, 2],
  "amount": 1000000,
  "collateral_token": "0x...",
  "parent_collection_id": "0x..."
}
```

Notes:
- `condition_id` and `parent_collection_id` must be 32-byte hex strings
- `partition` must contain at least two index sets (e.g. `[1, 2]`)
- `amount` is in smallest units (USDC has 6 decimals)
- `collateral_token` defaults to USDC if omitted
- `parent_collection_id` defaults to `0x00...00` if omitted

Response:
- Transaction receipt summary
中文说明：CTF 拆分（USDC -> YES/NO），返回交易回执摘要。

### 11) CTF Merge (YES/NO -> USDC)

- `POST /ctf/merge`

Body:
```json
{
  "condition_id": "0x...",
  "partition": [1, 2],
  "amount": 1000000,
  "collateral_token": "0x...",
  "parent_collection_id": "0x..."
}
```

Notes:
- Same validation rules as `/ctf/split`

Response:
- Transaction receipt summary
中文说明：CTF 合并（YES/NO -> USDC），返回交易回执摘要。

## Environment Variables

- `POLYGON_WALLET_PRIVATE_KEY`: wallet private key for signing and CLOB auth
- `OPENAI_API_KEY`: required for LLM usage elsewhere in the repo
- `TOOL_API_KEY`: optional header-based auth for this service
中文说明：`POLYGON_WALLET_PRIVATE_KEY` 用于签名和 CLOB 认证；`TOOL_API_KEY` 用于接口鉴权。

## Run the Server

```bash
uvicorn scripts.python.server:app --host 0.0.0.0 --port 8000
```
