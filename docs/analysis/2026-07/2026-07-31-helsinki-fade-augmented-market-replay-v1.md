# Helsinki fade-augmented market replay v1

## 人话结论

- 冻结fade morphology概率接入后，概率质量改善，但28个date-X入场集合没有变化，所以ROI和错单数也没有变化。
- checkpoint accuracy：fade增强=91.99%，旧state=90.88%，market=91.99%。
- checkpoint Brier/logloss：fade增强=0.06125/0.19870，旧state=0.06773/0.21575，market=0.07641/0.24874。
- fade增强相对旧state的Brier delta=-0.00648 CI[-0.01298,-0.00065]；logloss delta=-0.01706 CI[-0.03194,-0.00324]。
- 相对market虽点估改善，Brier delta=-0.01516，CI[-0.05346,+0.01966]，仍跨0。

## ROI与明细

- 全部price保留：28笔，25胜/3错，投入$109.33，净$+15.67，ROI=14.33%，date CI[4.87%,21.82%]。
- 仅研究摘要cost<0.98：19笔，16胜/3错，净$+15.50，ROI=24.04%；不作为最终策略阈值。
- old/new均为28个date-X，common=28，仅1个改变首次入场时刻；没有新增或删除date-X，因此ROI delta=0。

## 场景正确率

- fade rows=70：accuracy 旧state=81.4% → fade增强=82.9%，market=92.9%；Brier 0.233 → 0.194，仍差于market 0.129。
- active first-after-source rows=231/4 dates：fade增强Brier=0.05944，market=0.05855，样本仍不足。
- path/peak/evidence/price和逐笔明细见generated目录的`scenario_scores.csv`、`fade_trade_slices.csv`、`trade_replay.csv`。

## 资格

- 这是已看过7/15–29窗口的开发回放；fade artifact本身冻结于2025，但该市场窗口参与过结构诊断，不能冒充clean forward。
- date-X entry Brier fade增强=0.07185，market=0.08180；仍只有9个OOF market dates。
- probability relative old state=PASS；market baseline=FAIL_CI；clean forward=NA；结论inconclusive，保持zero-notional，不改live。
