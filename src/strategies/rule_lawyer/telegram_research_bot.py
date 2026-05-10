from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.platform.clients.polymarket_data import PolymarketDataClient
from src.platform.clients.telegram_client import TelegramClient
from src.strategies.rule_lawyer.adapters.rule_analysis_adapter import run_rule_analysis
from src.strategies.rule_lawyer.services.comment_intel import collect_market_comments
from src.strategies.rule_lawyer.services.market_analysis import build_market_intel_summary
from src.strategies.rule_lawyer.services.market_resolver import resolve_market


logger = logging.getLogger("telegram_research_bot")

COMMAND_RE = re.compile(r"^/(full|prompt|help|start)(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
MAX_MESSAGE_LEN = 3900


@dataclass
class ResearchBotConfig:
    bot_token: str
    allowed_chat_ids: set[str]
    api_base_url: str = "https://api.telegram.org"
    language: str = "zh"
    default_mode: str = "full"
    allow_rule_fallback: bool = True
    poll_timeout_sec: int = 20
    poll_interval_sec: float = 1.0

    @classmethod
    def from_env(cls) -> "ResearchBotConfig":
        bot_token = str(os.getenv("TG_RESEARCH_BOT_TOKEN") or "").strip()
        if not bot_token:
            raise ValueError("TG_RESEARCH_BOT_TOKEN is required")
        allowed_raw = str(os.getenv("TG_RESEARCH_ALLOWED_CHAT_IDS") or "").strip()
        if not allowed_raw:
            raise ValueError("TG_RESEARCH_ALLOWED_CHAT_IDS is required")
        allowed_chat_ids = {x.strip() for x in allowed_raw.split(",") if x.strip()}
        return cls(
            bot_token=bot_token,
            allowed_chat_ids=allowed_chat_ids,
            api_base_url=str(os.getenv("TG_RESEARCH_API_BASE_URL") or "https://api.telegram.org").strip(),
            language=str(os.getenv("TG_RESEARCH_LANG") or "zh").strip() or "zh",
            default_mode=str(os.getenv("TG_RESEARCH_DEFAULT_MODE") or "full").strip().lower() or "full",
            allow_rule_fallback=_env_bool("TG_RESEARCH_ALLOW_RULE_FALLBACK", True),
            poll_timeout_sec=max(1, _env_int("TG_RESEARCH_POLL_TIMEOUT_SEC", 20)),
            poll_interval_sec=max(0.1, _env_float("TG_RESEARCH_POLL_INTERVAL_SEC", 1.0)),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return float(raw)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _looks_like_market_target(text: str) -> bool:
    value = (text or "").strip()
    return bool(value) and (
        value.startswith("http://")
        or value.startswith("https://")
        or value.startswith("0x")
        or (" " not in value and "/" not in value)
    )


def parse_user_request(text: str, default_mode: str = "full") -> Tuple[str, str]:
    raw = (text or "").strip()
    if not raw:
        return "help", ""
    match = COMMAND_RE.match(raw)
    if match:
        mode = match.group(1).lower()
        target = (match.group(2) or "").strip()
        return mode, target
    if _looks_like_market_target(raw):
        return default_mode, raw
    return "help", ""


def _format_number(value: float) -> str:
    if value >= 1000000:
        return f"{value / 1000000:.2f}m"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def _utc_iso_from_ts(ts: Any) -> str:
    try:
        stamp = int(float(ts))
    except Exception:
        return "-"
    return datetime.fromtimestamp(stamp, UTC).isoformat().replace("+00:00", "Z")


def _chunk_text(text: str, limit: int = MAX_MESSAGE_LEN) -> List[str]:
    lines = text.splitlines()
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        addition = len(line) + (1 if current else 0)
        if current and current_len + addition > limit:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len += addition
    if current:
        chunks.append("\n".join(current))
    if not chunks:
        return [text[:limit]]
    return chunks


def _holders_summary(
    market: Dict[str, Any],
    data_client: Optional[PolymarketDataClient] = None,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    client = data_client or PolymarketDataClient()
    rows = client.get_market_holders(str(market.get("condition_id") or ""), limit=limit)
    prices = market.get("outcome_prices") or []
    outcomes = market.get("outcomes") or []
    summary: List[Dict[str, Any]] = []
    for group in rows:
        holders = [row for row in group.get("holders", []) if isinstance(row, dict)]
        if not holders:
            continue
        idx = int(_to_float(holders[0].get("outcomeIndex"), 0.0))
        price = prices[idx] if idx < len(prices) else 0.0
        shares = sum(_to_float(row.get("amount"), 0.0) for row in holders[:limit])
        top_names = []
        for row in holders[:5]:
            label = str(row.get("name") or row.get("pseudonym") or row.get("proxyWallet") or "").strip()
            if label:
                top_names.append(label)
        summary.append(
            {
                "outcome_index": idx,
                "outcome": outcomes[idx] if idx < len(outcomes) else f"Outcome {idx}",
                "holder_count": len(holders),
                "top_shares": round(shares, 3),
                "top_mark_to_mkt": round(shares * price, 2),
                "top_names": top_names,
            }
        )
    return summary


def _recent_trades_summary(
    condition_id: str,
    data_client: Optional[PolymarketDataClient] = None,
    *,
    limit: int = 80,
) -> Dict[str, Any]:
    client = data_client or PolymarketDataClient()
    rows = [row for row in client.get_market_trades(condition_id, limit=limit, offset=0) if isinstance(row, dict)]
    buckets: Dict[str, Dict[str, float]] = {}
    for row in rows:
        key = f"{row.get('side')} {row.get('outcome')}"
        item = buckets.setdefault(key, {"trades": 0.0, "shares": 0.0, "notional": 0.0})
        item["trades"] += 1.0
        size = _to_float(row.get("size"), 0.0)
        price = _to_float(row.get("price"), 0.0)
        item["shares"] += size
        item["notional"] += size * price
    timestamps = [int(_to_float(row.get("timestamp"), 0.0)) for row in rows if int(_to_float(row.get("timestamp"), 0.0)) > 0]
    return {
        "window_start_utc": _utc_iso_from_ts(min(timestamps)) if timestamps else "-",
        "window_end_utc": _utc_iso_from_ts(max(timestamps)) if timestamps else "-",
        "buckets": {
            key: {
                "trades": int(val["trades"]),
                "shares": round(val["shares"], 2),
                "notional": round(val["notional"], 2),
            }
            for key, val in sorted(buckets.items(), key=lambda item: item[1]["notional"], reverse=True)
        },
        "sample": [
            {
                "time": _utc_iso_from_ts(row.get("timestamp")),
                "side": str(row.get("side") or ""),
                "outcome": str(row.get("outcome") or ""),
                "price": round(_to_float(row.get("price"), 0.0), 4),
                "size": round(_to_float(row.get("size"), 0.0), 4),
                "name": str(row.get("name") or "").strip(),
            }
            for row in rows[:8]
        ],
    }


def collect_local_market_bundle(target_market: str, *, allow_rule_fallback: bool) -> Dict[str, Any]:
    market = resolve_market(target_market)
    rule_audit = run_rule_analysis(market, require_llm=not allow_rule_fallback).to_dict()
    smart_wallets = {
        "generated_from_market": market.to_dict(),
        "stats": {"candidate_wallets": 0, "selected_wallets": 0, "mode": "lightweight_top_wallets_only"},
        "wallets": [],
    }
    comments = collect_market_comments(
        market=market,
        comment_limit=15,
        mode="top_and_newest",
        wallet_score_lookup={},
    )
    market_intel = build_market_intel_summary(
        market=market.to_dict(),
        comments_result=comments,
        smart_wallets_result=smart_wallets,
        wallet_audits=[],
    ).to_dict()

    data_client = PolymarketDataClient()
    holders = _holders_summary(market.to_dict(), data_client=data_client, limit=20)
    trades = _recent_trades_summary(market.condition_id, data_client=data_client, limit=80)
    return {
        "market": market.to_dict(),
        "rule_audit": rule_audit,
        "comments": comments,
        "smart_wallets": smart_wallets,
        "market_intel": market_intel,
        "holders": holders,
        "trades": trades,
    }


def _holder_bias_line(holders: Sequence[Dict[str, Any]]) -> str:
    if len(holders) < 2:
        return "holders 数据不足。"
    ordered = sorted(holders, key=lambda item: item.get("top_mark_to_mkt", 0.0), reverse=True)
    lead = ordered[0]
    runner = ordered[1]
    return (
        f"Top holders 当前更集中在 {lead.get('outcome')}，top20 估算市值约 {lead.get('top_mark_to_mkt'):.2f}，"
        f"对比 {runner.get('outcome')} 的 {runner.get('top_mark_to_mkt'):.2f}。"
    )


def _trade_bias_line(trades: Dict[str, Any]) -> str:
    buckets = trades.get("buckets") or {}
    if not buckets:
        return "近期成交样本不足。"
    first_key = next(iter(buckets.keys()))
    top = buckets[first_key]
    return (
        f"最近成交窗口 {trades.get('window_start_utc')} -> {trades.get('window_end_utc')}，"
        f"名义金额最大的方向是 {first_key}，约 {top.get('notional', 0.0):.2f}。"
    )


def _rule_takeaway(bundle: Dict[str, Any]) -> str:
    rule = bundle.get("rule_audit") or {}
    status = str(rule.get("rule_status") or "unknown")
    clarity = _to_float(rule.get("rule_clarity_score"), 0.0)
    risk = _to_float(rule.get("resolution_risk"), 0.0)
    if status != "ok":
        return "规则层这次没有完整跑通，所以这份结论只能当作参考，不能直接拿来下交易决定。"
    if risk >= 0.75 or clarity <= 0.35:
        return "这个盘的结算边界偏危险，规则本身就有较强歧义，先别急着相信站内价格。"
    if risk >= 0.55 or clarity <= 0.55:
        return "规则大体可读，但边界位仍要小心，尤其是新闻标题和实际结算条件不完全一致的时候。"
    return "规则层整体算清楚，至少不会因为明显的结算歧义把结论带偏。"


def _comment_takeaway(comments: Dict[str, Any]) -> str:
    status = str(comments.get("comment_status") or "unavailable")
    stats = comments.get("comment_stats") or {}
    total = int(_to_float(stats.get("total_comments"), 0.0))
    bullish = int(_to_float(stats.get("bullish_comments"), 0.0))
    bearish = int(_to_float(stats.get("bearish_comments"), 0.0))
    if status != "ok":
        return "这个盘当前没有拿到可用评论区，所以不能参考社区讨论热度，只能更多看价格、holders 和近期成交。"
    return f"评论区可用，样本约 {total} 条；其中偏 Yes 线索 {bullish} 条，偏 No 线索 {bearish} 条。"


def _holders_takeaway(holders: Sequence[Dict[str, Any]]) -> str:
    if len(holders) < 2:
        return "暂时没有足够的 holder 结构样本。"
    ordered = sorted(holders, key=lambda item: item.get("top_mark_to_mkt", 0.0), reverse=True)
    lead = ordered[0]
    runner = ordered[1]
    gap = _to_float(lead.get("top_mark_to_mkt"), 0.0) - _to_float(runner.get("top_mark_to_mkt"), 0.0)
    return (
        f"大户筹码目前更集中在 {lead.get('outcome')}，top20 估算市值约 {lead.get('top_mark_to_mkt'):.2f}；"
        f"另一边约 {runner.get('top_mark_to_mkt'):.2f}，差值约 {gap:.2f}。"
    )


def _trade_takeaway(trades: Dict[str, Any]) -> str:
    buckets = trades.get("buckets") or {}
    if not buckets:
        return "近期成交样本不足。"
    buy_yes = _to_float((buckets.get("BUY Yes") or {}).get("notional"), 0.0)
    buy_no = _to_float((buckets.get("BUY No") or {}).get("notional"), 0.0)
    if buy_yes > buy_no * 1.4:
        direction = "近期主动成交更偏向 Yes"
    elif buy_no > buy_yes * 1.4:
        direction = "近期主动成交更偏向 No"
    else:
        direction = "近期主动成交没有明显单边"
    return f"{direction}。{_trade_bias_line(trades)}"


def _verdict_explanation(bundle: Dict[str, Any]) -> str:
    intel = bundle.get("market_intel") or {}
    observed_bias = _to_float(intel.get("observed_bias"), 0.0)
    local_view = _local_view(bundle)
    if observed_bias >= 0.2:
        bias_text = "站内证据明显更偏 Yes"
    elif observed_bias <= -0.2:
        bias_text = "站内证据明显更偏 No"
    else:
        bias_text = "站内证据没有形成特别强的单边倾向"
    return f"{bias_text}。{local_view}"


def _local_view(bundle: Dict[str, Any]) -> str:
    intel = bundle.get("market_intel") or {}
    holders = bundle.get("holders") or []
    observed_bias = _to_float(intel.get("observed_bias"), 0.0)
    score_yes = 0
    score_no = 0
    if observed_bias >= 0.12:
        score_yes += 1
    elif observed_bias <= -0.12:
        score_no += 1
    if len(holders) >= 2:
        ordered = sorted(holders, key=lambda item: item.get("top_mark_to_mkt", 0.0), reverse=True)
        if ordered[0].get("outcome") == "Yes" and ordered[0].get("top_mark_to_mkt", 0.0) > ordered[1].get("top_mark_to_mkt", 0.0) * 1.3:
            score_yes += 1
        if ordered[0].get("outcome") == "No" and ordered[0].get("top_mark_to_mkt", 0.0) > ordered[1].get("top_mark_to_mkt", 0.0) * 1.3:
            score_no += 1
    trade_buckets = bundle.get("trades", {}).get("buckets") or {}
    buy_yes = _to_float((trade_buckets.get("BUY Yes") or {}).get("notional"), 0.0)
    buy_no = _to_float((trade_buckets.get("BUY No") or {}).get("notional"), 0.0)
    if buy_yes > buy_no * 1.4:
        score_yes += 1
    elif buy_no > buy_yes * 1.4:
        score_no += 1

    if score_yes >= score_no + 1:
        return "本地证据层暂偏 Yes，但仍需外部新闻确认催化剂。"
    if score_no >= score_yes + 1:
        return "本地证据层暂偏 No，主要靠 holder / trade flow 支撑。"
    return "本地证据层偏混合，单靠站内数据不够形成强观点。"


def build_full_analysis_text(bundle: Dict[str, Any]) -> str:
    market = bundle["market"]
    raw_market = market.get("raw_market") or {}
    intel = bundle["market_intel"]
    comments = bundle["comments"]
    holders = bundle["holders"]
    trades = bundle["trades"]
    yes_price = _to_float((market.get("outcome_prices") or [0.0])[0], 0.0)
    no_price = _to_float((market.get("outcome_prices") or [0.0, 0.0])[1], 0.0)
    price_move_1d = _to_float(raw_market.get("oneDayPriceChange"), 0.0)
    yes_bid = _to_float(raw_market.get("bestBid"), 0.0)
    yes_ask = _to_float(raw_market.get("bestAsk"), 0.0)
    spread = max(0.0, yes_ask - yes_bid) if yes_bid > 0 and yes_ask > 0 else 0.0
    summary = (bundle.get("rule_audit", {}).get("rule_summary") or market.get("description") or "").strip()
    lines = [
        f"【{market.get('question') or market.get('slug')}】",
        "",
        "结论",
        f"- {_verdict_explanation(bundle)}",
        "- 这份输出只基于本地数据层，不包含外部实时新闻搜索。",
        "",
        "我为什么这么看",
        f"- {_rule_takeaway(bundle)}",
        f"- {_holders_takeaway(holders)}",
        f"- {_trade_takeaway(trades)}",
        f"- {_comment_takeaway(comments)}",
        "",
        "盘面快照",
        f"- 当前价格大约是 Yes {yes_price:.3f} / No {no_price:.3f}。",
        f"- 当前盘口大约是 bid {yes_bid:.3f} / ask {yes_ask:.3f}，spread 约 {spread:.3f}。",
        f"- 24h 成交量约 {_format_number(_to_float(raw_market.get('volume24hr'), 0.0))}，累计成交量约 {_format_number(_to_float(market.get('volume'), 0.0))}，流动性约 {_format_number(_to_float(market.get('liquidity'), 0.0))}。",
        f"- 近 1 天价格变动约 {price_move_1d:+.3f}，截止时间是 {market.get('end_date') or '-'}。",
        "",
        "规则怎么理解",
        f"- {summary[:280]}",
        "",
        "还缺什么",
        "- 如果你要拿来交易，下一步最好用 /prompt 同一链接，把这份本地 fact pack 交给 ChatGPT 联网补外部新闻和最新催化剂。",
        "- 当前第一版 wallet 层默认是轻量模式，更偏 top holders / recent trades，不是完整 smart money 审计。",
    ]
    return "\n".join(lines)


def build_prompt_handoff_text(bundle: Dict[str, Any]) -> str:
    market = bundle["market"]
    raw_market = market.get("raw_market") or {}
    comments = bundle["comments"]
    smart_wallets = bundle["smart_wallets"]
    holders = bundle["holders"]
    trades = bundle["trades"]
    top_comments = comments.get("top_commentary") or []
    top_wallets = smart_wallets.get("wallets") or []
    today = datetime.now(UTC).date().isoformat()
    lines = [
        "请基于下面的本地 fact pack，对这个 Polymarket 市场继续做联网研究。",
        "",
        "要求：",
        f"1. 你必须联网核实截至 {today} 的最新公开信息，不要只复述本地 fact pack。",
        "2. 先确认结算规则真正要求的触发事件，不要把相关但不结算的新闻混进去。",
        "3. 优先看官方来源、主流通讯社、公司/政府公告，再看高质量二级媒体。",
        "4. 输出中文，并按下面结构给结论：结论 / 规则风险 / 最新外部信息 / 站内筹码与流动性 / 场景树 / 交易建议。",
        "5. 明确区分：已发生、可信传闻、推测三层。",
        "",
        "本地 FACT PACK：",
        f"- 市场：{market.get('question') or market.get('slug')}",
        f"- slug：{market.get('slug')}",
        f"- market_url：{market.get('market_url')}",
        f"- 当前价格：Yes {_to_float((market.get('outcome_prices') or [0.0])[0], 0.0):.3f} / No {_to_float((market.get('outcome_prices') or [0.0, 0.0])[1], 0.0):.3f}",
        f"- bid/ask：{_to_float(raw_market.get('bestBid'), 0.0):.3f} / {_to_float(raw_market.get('bestAsk'), 0.0):.3f}",
        f"- 24h volume：{_to_float(raw_market.get('volume24hr'), 0.0):.2f}",
        f"- total volume：{_to_float(market.get('volume'), 0.0):.2f}",
        f"- liquidity：{_to_float(market.get('liquidity'), 0.0):.2f}",
        f"- 截止时间：{market.get('end_date') or '-'}",
        f"- 规则摘要：{(bundle.get('rule_audit', {}).get('rule_summary') or market.get('description') or '').strip()}",
        f"- 规则状态：status={bundle.get('rule_audit', {}).get('rule_status')}, clarity={_to_float(bundle.get('rule_audit', {}).get('rule_clarity_score'), 0.0):.2f}, resolution_risk={_to_float(bundle.get('rule_audit', {}).get('resolution_risk'), 0.0):.2f}",
        f"- comments 状态：{comments.get('comment_status')}，统计={comments.get('comment_stats')}",
        f"- smart wallets：{len(top_wallets)} 个（第一版默认轻量模式，重点依赖 top holders），observed_bias={_to_float((bundle.get('market_intel') or {}).get('observed_bias'), 0.0):+.2f}",
        f"- holders 摘要：{holders}",
        f"- recent trades 摘要：{trades.get('buckets')}",
        "",
        "Top comments：",
    ]
    if top_comments:
        for row in top_comments[:5]:
            body = str(row.get("body") or "").strip().replace("\n", " ")
            lines.append(
                f"- [{row.get('bias_direction')}/{row.get('classification')}/score={_to_float(row.get('value_score'), 0.0):.1f}] {body[:280]}"
            )
    else:
        lines.append("- 无可用 comments。")

    lines.extend(["", "Top smart wallets："])
    if top_wallets:
        primary_token = str((market.get("token_ids") or [""])[0])
        for row in top_wallets[:5]:
            conviction = _to_float((row.get("token_convictions") or {}).get(primary_token), 0.0)
            lines.append(
                f"- {row.get('wallet')} | score={_to_float(row.get('score'), 0.0):.2f} | win_rate={_to_float(row.get('win_rate'), 0.0):.2f} | style={row.get('style_label')} | conviction={conviction:+.2f}"
            )
    else:
        lines.append("- 无合格 wallet 样本。")

    lines.extend(
        [
            "",
            "请特别回答：",
            "- 这个盘现在最大的外部信息缺口是什么？",
            "- 市场价格是否高估/低估了最新新闻进展？",
            "- 哪个未来催化剂会让价格最快重估？",
            "- 如果只能给一个交易动作，你会给什么，为什么？",
        ]
    )
    return "\n".join(lines)


class TelegramResearchBot:
    def __init__(self, config: ResearchBotConfig) -> None:
        self._config = config
        self._offset: Optional[int] = None

    async def run(self) -> None:
        async with TelegramClient(
            bot_token=self._config.bot_token,
            api_base_url=self._config.api_base_url,
            timeout_seconds=self._config.poll_timeout_sec + 5.0,
        ) as client:
            me = await client.get_me()
            logger.info("telegram research bot started: @%s", ((me.get("result") or {}).get("username") or "unknown"))
            while True:
                try:
                    updates = await client.get_updates(
                        offset=self._offset,
                        timeout=self._config.poll_timeout_sec,
                        allowed_updates=["message"],
                    )
                except Exception as exc:
                    logger.warning("telegram polling failed: %s", exc)
                    await asyncio.sleep(max(1.0, self._config.poll_interval_sec))
                    continue
                for row in updates.get("result", []):
                    if not isinstance(row, dict):
                        continue
                    update_id = int(_to_float(row.get("update_id"), 0.0))
                    self._offset = update_id + 1
                    try:
                        await self._handle_update(client, row)
                    except Exception as exc:
                        logger.exception("telegram update handling failed: %s", exc)
                await asyncio.sleep(self._config.poll_interval_sec)

    async def _handle_update(self, client: TelegramClient, update: Dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id or chat_id not in self._config.allowed_chat_ids:
            logger.info("ignored update from unauthorized chat: %s", chat_id or "unknown")
            return
        text = str(message.get("text") or "").strip()
        mode, target = parse_user_request(text, default_mode=self._config.default_mode)
        if mode in {"help", "start"}:
            await self._send_chunks(client, chat_id, self._help_text())
            return
        if not target:
            await self._send_chunks(client, chat_id, "缺少市场 URL / slug / condition id。\n\n示例：/full https://polymarket.com/event/...\n示例：/prompt will-fed-cut-rates")
            return

        await self._send_chunks(client, chat_id, f"收到，正在运行 `{mode}` 模式，本地抓取盘口与研究数据中……")
        try:
            bundle = await asyncio.to_thread(
                collect_local_market_bundle,
                target,
                allow_rule_fallback=self._config.allow_rule_fallback,
            )
            if mode == "prompt":
                output = build_prompt_handoff_text(bundle)
            else:
                output = build_full_analysis_text(bundle)
            await self._send_chunks(client, chat_id, output)
        except Exception as exc:
            logger.exception("telegram research command failed")
            await self._send_chunks(client, chat_id, f"执行失败：{exc}")

    async def _send_chunks(self, client: TelegramClient, chat_id: str, text: str) -> None:
        chunks = _chunk_text(text)
        total = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            payload = chunk if total == 1 else f"[{idx}/{total}]\n{chunk}"
            await client.send_message(chat_id=chat_id, text=payload)

    @staticmethod
    def _help_text() -> str:
        return (
            "可用命令：\n"
            "/full <url|slug|condition_id>  直接输出本地完整分析\n"
            "/prompt <url|slug|condition_id>  输出可贴给 ChatGPT 的 handoff prompt\n"
            "\n"
            "也支持直接发一个 Polymarket 链接，默认按 /full 处理。"
        )


async def run_bot_from_env() -> None:
    config = ResearchBotConfig.from_env()
    bot = TelegramResearchBot(config)
    await bot.run()
