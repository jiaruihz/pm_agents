# Manila Core carry 2026-09-07 逐笔复盘

只读诊断；未修改代码、配置或生产。时间均为北京时间/Manila UTC+8。near-core 样本截止 15:21:45，后续新增行不计本次统计。

## 结论

27 档 YES：Core taker 真实提交回执 matched，10 shares @0.86，makingAmount=8.6/takingAmount=10；普通 maker 的同一个5-share预算挂出三张顺序替换订单，未成交后TTL撤销；独立 near-core v2没有生成plan或挂单，受到错误的负book-age拦截。

## 时间线

| 时间 | 事实 |
|---|---|
|13:35:22|13点checkpoint，p=0.60287733，扣费深度edge=-0.01979675，未触发。|
|14:38:44 / 14:39:44|14点天气快照/评分写入；14:00 METAR、当前26、running max 27；p=0.84627760，ask=0.86，edge=-0.01974240。|
|14:40:24 / 14:40:29|near-core第一候选周期时钟/实际盘口抓取时钟；p=0.85150501、bid/ask=0.745/0.86、edge=-0.01451499；age=-4.988348s，被direct_book_clock_in_future阻断。|
|14:40:59 / 14:41:06|near-core第二候选，p=0.86118160，bid/ask=0.771/0.86，age=-6.757272s，同样阻断。|
|14:41:40 / 14:41:48|连续盘口重评周期/盘口抓取，p=0.86859971，bid/ask=0.791/0.86，扣费深度edge=+0.00257971，生成10+5计划。计划created_at沿用周期开始时间，不能当实际完成时间。|
|14:42:10|taker提交记录及exchange matched回执，10@0.86，$8.60成交本金。该时间为本地提交回执记录，未独立核验链上block timestamp。|
|14:42:13|普通maker获live回执，5@0.792；fresh bid/ask=0.791/0.899，cap=0.858。|
|14:42:29–14:46:34|queue_preserving_stage，保留初始订单，不追价。|
|14:47:10 / 14:47:37|midpoint决策next_price=0.858；实际替换挂出5@0.812，bid/ask=0.811/0.906，前单source_filled=0、remaining=5。|
|14:47:53 / 14:48:18|第二次改价；实际5@0.814，bid/ask=0.813/0.907，前单source_filled=0、remaining=5。|
|14:48:34–14:56:17|maker_reprice_limit_reached，count=2/max=2；最后单保持0.814，抽样ask=0.900–0.907。|
|14:56:40|15分钟maker lifecycle deadline（从14:41:40计划时钟算）。|
|14:56:57 / 14:57:26|TTL撤单决策/成功cancel回执；source_filled=0、remaining=5。并非14:58:30 pre-observation deadline触发。|
|截至15:21:45|near-core当日Manila ledger 53条，全部blocked/direct_book_clock_in_future，0 plan；其中2条在Core入场前。后续已有Core exposure，不能把53条当53笔可成交漏单。|

## 根因与边界

生产main PID70986加载core_carry_runtime/a9cc1bea44d9dacd0fcef9165446377fc001a6f3；live执行release登记b65282a633e9e5eb0d6f74284d2542c7cadb6d0f。strict manifest命令exit0。main参数包含--live --confirm-live --near-core-maker-probe-enabled --confirm-near-core-maker-probe-live；v2 manifest的feature_enabled/live_authorized均true。

生产脚本weather_current_yes_core_carry_tiny_live_v2.py:2972在continuous_market_rescores抓取盘口前采样now，随后把同一个now传入near_core_entry_plans。该函数:2121调用near_core_book_freshness；:285将age<0直接判为future。盘口实际在后面抓取，因此这是本地决策时钟顺序错误。ledger live_enabled=False由bool(plan and plan.get("live_enabled"))计算，表示没有live plan，不能解读为部署开关关闭。freshness先于existing_core_family_exposure检查，入场后的阻断记录还遮住了后续exposure blocker。

普通maker另有决策价/执行价差异：lifecycle next_price=0.858，但实际请求与posted分别为0.812/0.814，quote_mode=fresh_bid_improve_one_tick_post_only_retained_edge_capped。两次改价耗尽后不再追价。raw足以证明行为；本次未进一步裁定该价格映射是否违背现行合同，也不能从这些稀疏盘口断言改成0.858就会成交。

本次未做修复、全历史反事实重放、canonical rebuild或PnL结论。53条是阻断决策数；实际漏掉的可成交股数未知。

## 原始证据

根目录：/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/

- pre_live_scores.jsonl:8369、8386
- continuous_market_rescores.jsonl:52882、52964
- entry_attempts.jsonl:238，signal=current-yes-core-carry-1e63c27b42fc29a59686ce80
- near_core_maker_v2_ledger.jsonl:1516、1519，样本至1729
- live_orders.jsonl:596–600
- maker_lifecycle_decisions.jsonl:584605、584647、584659、584731
- near_core_maker_v2_manifest.json

订单：
- taker: 0x18a14660c50a80a206e19568163601f1db4ab4c4e2f7ca5153088f8fbd4fe823
- maker root: 0x2db441986004410bf9db6a076e2394279b65e9b76cc8450fd88a44cdddba35b3
- replacement1: 0xc32c0b751c1aa9964014ab444e62671381c9a82894c138d33140aefce89c3643
- replacement2: 0xb76c5a386e9bc1e3527561935fb632c91622da99bbcc4104549a3927b52e957c
- taker exchange transaction hash: 0xffb207becbd00c48ef94b1db3448cff4c813aafe4f505f7dcddf34e0ccbd9bbe


## 用户授权修复与部署（2026-09-07，后续补充）

授权：用户明确要求“整个链路修复完成并且部署”。此前“只读、未修复”描述只适用于首轮诊断。

### 为什么没有新 METAR 也能转正

`market_features` 使用 `(bid+ask)/2` 的 `market_logit`，与冻结小时桶、露点差、风速等一起计算概率。连续重评保留天气状态，用直接 REST 盘口重新运行同一冻结模型，并以10股ask ladder本金+逐档fee计算EV。因此它不是weather-only概率，也不是必须新METAR才触发。Manila同一14:00 METAR/14:38快照期间，ask维持0.86、bid依次0.745→0.771→0.791，模型p为0.851505→0.861182→0.868600；最后edge=+0.00257971。模型、信号机制与概率输入本次未调整。

### 修复范围

1. REST producer记录microsecond请求开始、响应收到、解析完成时钟；直接quote替换时同步替换全部clock字段，清除继承paper/exchange时钟，标记`direct_clob_response_clock_v1`。
2. rescore的created/market_rescore时间使用实际评分完成时间，另保留cycle_started；Core计划、near entry、两条maker lifecycle与post-update revalidation在前序I/O后分别采样decision now，TTL从真实计划时钟开始。
3. 保留真实future、missing、非ok与超过10秒的盘口阻断；near ledger新增独立live_authorized，避免把plan不存在误读为实盘开关关闭。
4. 第一版baa811f8上线后已消除negative-age，但运行中仍出现14–19秒真实stale。隔离runtime计时显示recover/recover-near/new-entry/near-lifecycle分别约0.044/0.074/0.888/0.366秒；主要延迟是串行读取剩余城市盘口。最终28d4870a使用最多4线程读取同一批候选，保持最大64 books/cycle与确定性处理顺序；状态恢复移至quote前。未提高10秒阈值，未增加fallback，未更改股票数量或maker报价政策。
5. 并发真实REST smoke：42 rows、6.975秒，检查时42/42盘口fresh、age 0.556–4.059秒；仅抓取评分，没有调用执行。

### 历史影响与边界

污染记录窗口：2026-09-06 17:30:46 UTC至2026-09-07 07:52:46 UTC（北京时间9/7 01:30:46–15:52:46）。1893条future-block全部找到原始rescore，涉及19个city-days，Manila99条。期间near-core真实提交0、由本bug错误提交的orders/fills=0；受污染的是阻断原因与信号/盘口时钟，不是已成交PnL。

旧direct fetched_at实际为请求开始，缺response完成时间。可审计条件重放以“同cycle最后请求开始”为决策下界，以首个后续summary为完成上界。下界结果：1323 stale、397其他maker安全条件、81已有Core exposure、92条条件下可生成计划；92条覆盖10个city-days（Chicago、SanFrancisco、NYC、Seattle、LA、Wellington、Busan、Shanghai、Singapore、Manila）。全部在完成上界已stale，因此不能精确重建当时fresh窗口。最多一个city-day的5股probe，不可把1893次重复判断等同1893笔漏单。hypothetical private readiness、queue/fill hazard缺失，确定漏成交股数、fee与PnL delta均unknown，不伪造为0。最终并发调度改变响应排列，无法仅由旧serial记录重造真实counterfactual fills。

逐条清单、原始rescore副本、条件重放脚本、检查与review evidence均在 `/Users/deepsleep/.local/share/pm_agents/artifacts/core-clock-fix-20260907/`。未rewrite原始日志、未全量rebuild、未重算canonical PnL。

### Review与验证

单独read-only reviewer角色gpt-5.6-luna/medium，审阅clock producer/runner/新增clock tests，报告无finding，compile与diff-check通过。reviewer使用系统Python未找到pytest，主agent使用项目`.venv`完成测试。rollout核验：input610478、cached564992、output2925、total613403；证据见review.json，已结束，不复用或追加reviewer。

Review后的主agent优化：通过真实运行发现并修复serial batch等待；self-review捕获并修正并发重构中的city-day hash归属，增加并发上限、确定性顺序、rescore identity断言。最终五组相关测试104 passed（含partial fill、cancel/replace、recovery、freshness、REST时钟与并发），py_compile与git diff --check通过。没有把编译成功当成live成交验收。

### Git与回滚

初版source baa811f837f691e7117efb925ef1cf33d1bc6c8c；最终source 28d4870a3244ac235d012e630c9e50dbb7f290b6。控制仓库提交76bcc119 / d1975422仅整合本次代码与production pin；无关dirty/staged文件保留。

最终production release：core_carry_runtime/28d4870a3244ac235d012e630c9e50dbb7f290b6。主进程与near safety controller共用该release，由已提交control_plane 6ac3384d的controller restart合同重载。旧release a9cc1bea保留；若回滚，将core_carry_runtime pin恢复该full SHA并由同一controller对上述两实例执行已授权guarded restart，复核session集合与exchange。

原始资金合同保留：Core10 taker+5 maker、near独立5股；Core10city-days/$100，near10city-days/$50；post-only、15分钟TTL、10秒book freshness、现有WS safety cancel。未更改普通maker两次reprice政策，未将研究promotion gate标为accepted。

部署最终验收见下方补充。


### 最终生产验收（16:07北京时间）

- Mac main PID94179、near safety PID85779均加载full SHA `28d4870a3244ac235d012e630c9e50dbb7f290b6`，raw summary同SHA、status=ok、live=true、near control failures=[]；安全控制器ready。
- 最终版本08:02:15 UTC重载，至08:05:12收集211条rescore，request→response→parsed→score时钟逆序0。16条near决策全部fresh，age4.808–7.181秒、future-block0；11条maker安全条件不满足，5条已有Core exposure。Manila现在因已有Core仓位正确阻断，不补发重复near单。没有人为制造真实订单来验收。
- strict manifest与pre-change比较通过：41个持久session全部保留、missing_sessions=[]。初始额外reliability bounded worker自然结束，由manifest的既有bounded分类排除；不是业务session丢失。仅保留已有unregistered worktree warning。
- controller health最终08:07:04 UTC为warning、critical_runtimes=[]；Core主进程、near控制器及两个Core shadow依赖均healthy。曾在base producer临时写入summary期间看到health_live_not_enabled，待完整主循环发布后恢复；该既有summary发布窗口未在此次改变。
- API `/health`返回ok。部署前后authenticated open-order列表均21笔，当前Core日志对应open orders=0；四笔Manila历史order lookup未返回，历史matched/cancel仍以已保留exchange回执为证据，未伪称重新查到历史fills。
- 最终104 tests passed，生产与控制仓库三个修改文件内容一致。资金/模型/policy未漂移。pre/final manifest、health、controller动作、进程参数、REST smoke、exchange和逐条raw evidence均已归档。
- 本次创建的开发worktree、初版baa811f8 release与最终28d4870a release保留。最终release仍被production/PID引用，不是cleanup候选；初版release已无活跃PID引用，可作后续archive候选。开发worktree保留报告/governance提交；未删除、prune或迁走任何无关改动。inventory见worktree-inventory.json。
