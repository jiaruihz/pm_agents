# WCIR Matched Denominator Contract V1

共同 denominator 由 event-level market-data eligibility 决定，不按某个 policy 是否实际交易删行。`NO_TRADE`/abstain 以 0 PnL 留在 denominator。不同 policy 可以选择不同 token；“必须选择同一个 token”不是进入 denominator 的条件。

比较 oracle 与 baseline 时，如双方所需输入不同，另报 pairwise common denominator，但不得覆盖 full event denominator。每个结果同时报告 raw event N、unique official-print N、target-date N、effective N（`target_date × official_print_id`）。

当前 all-baseline exact intersection 只有 1 行，只用于完整性诊断，不能计算或宣称 alpha、futility、city ranking 或 promotion。缺行原因按非互斥 histogram 报告：missing entry book、missing exit book、chosen token unavailable、feature unavailable、market-only OOF blocked、other。原因非互斥是有意的；一个 event 可能同时缺 market 和 feature。
