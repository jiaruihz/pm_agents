# Polymarket Tool Service API

This document describes the HTTP API exposed by `scripts/python/server.py`.

## Base URL

- Default (local): `http://localhost:8000`

## Auth

If the environment variable `TOOL_API_KEY` is set, all requests must include:

- Header: `X-API-Key: <your_key>`

If `TOOL_API_KEY` is not set, auth is not enforced.

## Endpoints

### 1) Health

- `GET /health`

Response:
```json
{"ok": true}
```

### 2) Events

- `GET /events`

Query parameters:
- `tradeable` (bool, default `false`)
- `limit` (int, optional)
- `offset` (int, default `0`)

Response:
- List of `SimpleEvent`

### 3) Markets

- `GET /markets`

Query parameters:
- `tradeable` (bool, default `false`)
- `limit` (int, optional)
- `offset` (int, default `0`)

Response:
- List of `SimpleMarket`

### 4) Single Market by token_id

- `GET /market/{token_id}`

Path parameters:
- `token_id`: CLOB token id

Response:
- `SimpleMarket`

### 5) Order Book

- `GET /orderbook/{token_id}`

Path parameters:
- `token_id`: CLOB token id

Response:
- CLOB order book object

### 6) Wallet Balance

- `GET /balance`

Response:
```json
{
  "address": "0x...",
  "usdc_balance": 123.45
}
```

### 7) Place Limit Order

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

### 8) Place Market Order (FOK)

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

### 9) CTF Split (USDC -> YES/NO)

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

### 10) CTF Merge (YES/NO -> USDC)

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

## Environment Variables

- `POLYGON_WALLET_PRIVATE_KEY`: wallet private key for signing and CLOB auth
- `OPENAI_API_KEY`: required for LLM usage elsewhere in the repo
- `TOOL_API_KEY`: optional header-based auth for this service

## Run the Server

```bash
uvicorn scripts.python.server:app --host 0.0.0.0 --port 8000
```
