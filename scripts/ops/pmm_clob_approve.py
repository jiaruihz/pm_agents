from __future__ import annotations

import argparse
import json
import os
from typing import List

from dotenv import load_dotenv
from web3 import Web3
from web3.middleware.proof_of_authority import ExtraDataToPOAMiddleware


load_dotenv()

USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DEFAULT_OPERATORS = [
    "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",  # exchange
    "0xC5d563A36AE78145C45a50134d48A1215220f80a",  # neg-risk exchange
    "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",  # neg-risk adapter
]
MAX_UINT256 = (1 << 256) - 1

ERC20_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

ERC1155_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "address", "name": "operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "operator", "type": "address"},
            {"internalType": "bool", "name": "approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def _parse_operators(raw: str) -> List[str]:
    items = [x.strip() for x in raw.split(",") if x.strip()]
    # Keep deterministic order and remove duplicates.
    seen = set()
    out = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _send_tx(w3: Web3, private_key: str, tx: dict) -> str:
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="pmm_clob_approve",
        description="Approve Polymarket USDC + CTF operators (check-only by default).",
    )
    p.add_argument("--rpc-url", default=os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"))
    p.add_argument("--chain-id", type=int, default=137)
    p.add_argument(
        "--private-key",
        default=os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip(),
        help="EOA private key (default: POLYGON_WALLET_PRIVATE_KEY -> PM)",
    )
    p.add_argument("--wallet-address", default=os.getenv("PM_ADDRESS", "").strip(), help="Optional address consistency check")
    p.add_argument("--usdc-address", default=USDC_ADDRESS)
    p.add_argument("--ctf-address", default=CTF_ADDRESS)
    p.add_argument("--operators", default=",".join(DEFAULT_OPERATORS), help="Comma-separated operator addresses")
    p.add_argument("--approve-amount", type=int, default=MAX_UINT256, help="USDC approve amount")
    p.add_argument("--min-allowance", type=int, default=10**18, help="If allowance >= this value, treat as approved")
    p.add_argument("--run", action="store_true", help="Actually send approval transactions")
    p.add_argument("--confirm", action="store_true", help="Required with --run")
    p.add_argument("--force", action="store_true", help="Send tx even if already approved")
    p.add_argument("--wait-receipt", action="store_true", help="Wait for each tx receipt")
    args = p.parse_args()

    if not args.private_key:
        print("[FATAL] missing private key: set PM or POLYGON_WALLET_PRIVATE_KEY")
        return 2
    if args.run and not args.confirm:
        print("[BLOCKED] --run requires --confirm")
        return 2

    operators = _parse_operators(args.operators)
    if not operators:
        print("[FATAL] no operators provided")
        return 2

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    if not w3.is_connected():
        print(f"[FATAL] cannot connect rpc: {args.rpc_url}")
        return 1

    acct = w3.eth.account.from_key(args.private_key)
    owner = acct.address
    if args.wallet_address and owner.lower() != args.wallet_address.lower():
        print(f"[WARN] derived address != PM_ADDRESS: {owner} != {args.wallet_address}")
    print(f"[INFO] owner={owner}")
    print(f"[INFO] chain_id={args.chain_id} rpc={args.rpc_url}")

    usdc = w3.eth.contract(address=Web3.to_checksum_address(args.usdc_address), abi=ERC20_ABI)
    ctf = w3.eth.contract(address=Web3.to_checksum_address(args.ctf_address), abi=ERC1155_ABI)

    status = {"owner": owner, "operators": []}
    plan = []
    for op in operators:
        op_cs = Web3.to_checksum_address(op)
        allowance = int(usdc.functions.allowance(owner, op_cs).call())
        approved_for_all = bool(ctf.functions.isApprovedForAll(owner, op_cs).call())
        need_usdc = allowance < int(args.min_allowance)
        need_ctf = not approved_for_all
        status["operators"].append(
            {
                "operator": op_cs,
                "allowance": str(allowance),
                "ctf_isApprovedForAll": approved_for_all,
                "need_usdc_approve": need_usdc,
                "need_ctf_approve": need_ctf,
            }
        )
        if args.force or need_usdc:
            plan.append(("usdc_approve", op_cs))
        if args.force or need_ctf:
            plan.append(("ctf_setApprovalForAll", op_cs))

    print("[INFO] current approval status:")
    print(json.dumps(status, ensure_ascii=False, indent=2))

    if not args.run:
        print("[SAFE] check-only mode. add --run --confirm to send transactions.")
        return 0

    if not plan:
        print("[OK] no approval tx needed.")
        return 0

    nonce = w3.eth.get_transaction_count(owner, "pending")
    gas_price = int(w3.eth.gas_price)
    print(f"[INFO] sending {len(plan)} txs, start_nonce={nonce}, gas_price={gas_price}")

    tx_hashes = []
    for action, op in plan:
        if action == "usdc_approve":
            fn = usdc.functions.approve(op, int(args.approve_amount))
            gas = int(fn.estimate_gas({"from": owner}) * 1.2)
            tx = fn.build_transaction(
                {
                    "from": owner,
                    "chainId": int(args.chain_id),
                    "nonce": nonce,
                    "gas": gas,
                    "gasPrice": gas_price,
                }
            )
        else:
            fn = ctf.functions.setApprovalForAll(op, True)
            gas = int(fn.estimate_gas({"from": owner}) * 1.2)
            tx = fn.build_transaction(
                {
                    "from": owner,
                    "chainId": int(args.chain_id),
                    "nonce": nonce,
                    "gas": gas,
                    "gasPrice": gas_price,
                }
            )
        tx_hash = _send_tx(w3, args.private_key, tx)
        print(f"[TX] {action} operator={op} nonce={nonce} tx_hash={tx_hash}")
        tx_hashes.append(tx_hash)
        if args.wait_receipt:
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            print(f"[RECEIPT] status={receipt.status} block={receipt.blockNumber} tx={tx_hash}")
            if receipt.status != 1:
                print("[ERR] receipt status != 1, stop")
                return 1
        nonce += 1

    print("[OK] submitted tx hashes:")
    print(json.dumps(tx_hashes, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
