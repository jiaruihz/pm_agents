# Copy-trade rule-edge wallet research - 2026-06-09

## Conclusion

This copy-trade branch should stay **manual / research-only**. The useful edge is not "copy any profitable wallet"; it is:

```text
rule-edge market shortlist -> top-holder discovery -> wallet style filter -> independent rule/fact thesis -> manual entry
```

The current best-fit markets are:

1. OpenAI 2026 hardware product categories.
2. AI model leaderboard markets, especially Google / Anthropic best or second-best by June 30.
3. Warner Bros acquisition close / no-listed-company close.

Fed decisions, market-cap / stock-price style markets, 90%+ near-binary legs, sports, weather, and pure crypto price markets are excluded from this workflow.

## Artifacts

- Evidence JSON: `docs/analysis/2026-06/2026-06-09-copy-trade-rule-edge-wallets.json`
- Repro script: `scripts/analysis/copy_trade_rule_edge_wallet_research.py`

Run command:

```bash
python3 scripts/analysis/copy_trade_rule_edge_wallet_research.py \
  --out docs/analysis/2026-06/2026-06-09-copy-trade-rule-edge-wallets.json \
  --holders-per-market 25 \
  --max-wallets 30 \
  --max-closed-rows 300
```

Run result:

```text
markets=36
wallets_found=550
reviewed=30
```

## Research Procedure

### 1. Market filter

Market inclusion:

- Polymarket rule/definition markets where casual traders may misread settlement.
- Active markets with at least one side between `8c` and `88c`.
- Minimum market liquidity `>= 500`.
- Source event themes:
  - `what-kind-of-product-will-openai-announce-in-2026`
  - `which-company-has-second-best-ai-model-end-of-june`
  - `which-company-has-best-ai-model-end-of-june`
  - `who-will-close-warner-bros-acquisition`
  - `will-anthropic-or-openai-ipo-first`
  - `time-person-of-the-year-2026`

Market exclusion:

- Fed / macro-rate decisions.
- Stock price or market-cap forecasts.
- Sports, weather, ordinary elections, pure crypto price markets.
- 90%+ near-binary legs unless there is a clear rule discrepancy.

### 2. Wallet discovery

For each included market:

- Pull `/holders?market=<condition_id>`.
- Keep top holders by token side.
- Record:
  - wallet
  - amount
  - outcome index and outcome label
  - source market
  - current market prices

Important fix in this run: holder direction is now mapped from `outcomeIndex` to market `outcomes`. Earlier summaries that only said "holder" were weaker because they did not distinguish YES vs NO.

### 3. Wallet review

For top candidate wallets:

- Pull up to 300 `/closed-positions` rows.
- Pull current open positions.
- Compute:
  - closed count
  - net PnL
  - non-election PnL
  - win rate
  - profit factor
  - topic distribution
  - sports / arbitrage-like ratio
  - single-event dependency
  - low-price / favorite-grinder entry distribution

Limitations:

- `closed_positions_count=300` with `history_truncated=true` is only a first-stage screen. It is not a full career audit.
- Many wallets show `win_rate=1.0` in closed-position snapshots. Treat this as a data-source warning, not proof of perfect trading skill.
- Top-holder data gives current token inventory, not trade timestamp or cost basis. Do not infer entry quality without trade-level data.

## Market Findings

| Market | Current useful legs | Why it fits this workflow |
|---|---|---|
| OpenAI hardware product type | phone 17.5c, computer 12.5c, watch 15.5c, HMD 11.5c, ring 11.5c, glasses 19c, earbuds/headphones 30.5c | Rule says public announcement is enough; release is not required. One device can satisfy multiple categories. |
| AI model best by June 30 | Google 9.5c, Anthropic 86.95c | Resolution is tied to LMArena Text Arena Overall at a fixed time. This is researchable with external leaderboard data. |
| AI model second-best by June 30 | Google 25.5c, Anthropic 68c | Same rule source; second-place market is less one-sided and may have better price space. |
| Warner Bros acquisition close | Paramount 73.5c, No listed company 14.5c | Rule requires close / control transfer, not announcement. Studios + streaming matter; linear TV-only transactions do not qualify. |
| Anthropic vs OpenAI IPO first | Anthropic 82.5c, OpenAI 17.5c | Potential rule edge around "IPO first", listing definitions, direct listing/SPAC treatment. Needs full rule audit before action. |
| TIME Person 2026 | many 9c-40c legs | Some definition edge, but low 24h volume and noisy public attention. Use as wallet-discovery input, not primary trade source. |

## Wallet Findings

### Tier A - candidate to track for manual follow

These are not automatic-copy wallets. They are wallets whose positions should trigger a rule/fact research check.

| Wallet | Name | Current relevant positions | Historical screen | Style read | Action |
|---|---|---|---|---|---|
| `0x736539924a5602b37a03a54fc12c1cc8f98964da` | `cqk` | YES OpenAI glasses; YES OpenAI phone; YES Google second-best AI model; NO OpenAI earbuds/headphones | 300 closed rows, truncated; net PnL `+43,427.94`, non-election PnL `+42,244.06`, sports ratio `0.003`, single-event dependency `0.056` | Strongest fit. Tech-heavy, low sports noise, concentrated enough to interpret. | Primary watch. Follow only when independent OpenAI hardware or LMArena thesis agrees. |
| `0x807ccea75c5f34b63728794f42fde9cb3ac6f1e3` | `CryptoGerm` | YES HMD; YES phone; YES necklace-style wearable; YES computer | 140 closed rows, not truncated; net PnL `+229.78`, PF `1.627`, sports ratio `0.014` | Small but verifiable sample; tech skew; low-price style. | Good manual-follow candidate for OpenAI hardware, but use small size because history is not exceptional. |
| `0xd8bcf0b7c03348686607d6e59abd74edff5967e2` | `traboukos` | NO phone; NO computer; NO necklace; NO watch | 43 closed rows, not truncated; net PnL `+15,929.59`, PF `33.734`, sports ratio `0.279` | High-conviction counter-thesis to OpenAI hardware categories; sample is small and sports noise is not trivial. | Useful as opposing signal. Do not follow blindly; compare against `cqk` / `CryptoGerm` side. |
| `0x1c266db0f8529b1f25b77123e9c0c918ac2f6e31` | `5atka` | NO Paramount close; NO OpenAI glasses; YES OpenAI watch; NO no-listed-company close | 300 closed rows, truncated; net PnL `+29,763.28`, sports ratio `0.0` | Low sports noise and active in acquisition/rule markets, but direction is mixed. | Use for Warner Bros acquisition monitoring; requires full pagination. |

### Tier B - confirmation only

These wallets are useful as context but should not drive trades alone.

| Wallet | Reason |
|---|---|
| `0xad5353afe30c2da57709e2704ef3ccdcf67eef24` / `AJSV` | Appears in 25 source markets and holds many low-price YES OpenAI hardware legs. This may be a broad basket/scanner strategy rather than informed selection. |
| `0xcb14f61711d913773f4b982af340fa7eae175358` / `ooops` | Currently NO across many OpenAI hardware legs; historical screen is positive but truncated and has moderate sports exposure. |
| `0x74957ea27ac4fbdee46d861fdae357859ff67fcf` / `ultralisk` | YES earbuds/headphones and necklace, but lottery-like profile and mixed topic exposure. |
| `0x510f4963b66b1b18505faab74b0bb943d1dda43c` / `PPMT` | Some relevant OpenAI/WB positions, but history is truncated and favorite-grinder skew is visible. |
| `0x03805a13a0b3e058f55f6c6af95389d4f431073d` / `donthackme` | Large in Warner Bros acquisition and AI second-best markets; strong raw PnL but truncated and likely needs entity / event-dependency audit before trust. |

### Tier C - reject or ignore for this strategy

| Wallet | Reason |
|---|---|
| `0xa5e3044fd953605f9407d58c76e48fd75a394d7e` / `peepeepooppoop` | Sports ratio `0.5`, arbitrage-like ratio `0.507`. Not aligned with rule-edge copy-trading. |
| Very broad 300+ closed, perfect-win, many-topic wallets | Treat as systematic, market-making, or API-selection artifacts until trade-level data confirms real skill. |
| Low sample wallets under 30 closed positions | Can be observed, but not followed. |

## Strategy Recommendation

### Manual follow strategy

Use wallets as a trigger, not a decision engine:

1. Market must pass the rule-edge filter.
2. Wallet side must be explicit from `outcomeIndex`.
3. At least one Tier A wallet or two Tier B wallets must align on the same side.
4. Run independent rule/fact research before entry.
5. Entry price must still have room; avoid >85c unless the rule edge is unusually strong.
6. Size is capped by confidence:
   - `probe`: one wallet signal, no independent confirmation.
   - `standard`: Tier A wallet plus independent thesis.
   - `skip`: conflicting Tier A signals or unclear rule.

### Current research priorities

1. **OpenAI hardware basket**
   - `cqk`: YES glasses / phone, NO earbuds.
   - `CryptoGerm`: YES HMD / phone / necklace / computer.
   - `traboukos`: NO phone / computer / necklace / watch.
   - This is the best current arena because wallets disagree, rules are clear enough to research, and prices are not all near-binary.

2. **AI model leaderboard**
   - `cqk`: YES Google second-best.
   - `donthackme`: YES Google second-best, NO Anthropic second-best.
   - Must be checked against current LMArena rank and model-release calendar. Wallet signal alone is not enough.

3. **Warner Bros acquisition close**
   - `donthackme`: YES no-listed-company, YES Paramount.
   - `5atka`: NO Paramount, NO no-listed-company.
   - This conflict suggests the market may be useful for rule/fact research, but not for direct following yet.

## Next Step

Before any manual trade, run a deep-dive on one market family:

```text
OpenAI hardware -> pull current holder deltas -> read full market rules -> collect official OpenAI / Jony Ive product evidence -> decide max entry price per category
```

The first deep-dive should focus on:

- YES OpenAI glasses
- YES/NO OpenAI phone
- YES HMD
- YES/NO earbuds/headphones

because these are where the highest-signal wallets currently disagree.
