# Helsinki fade mode 与入场时机 v1

## 结论

- 2025固定OOF分母：51,451 checkpoints/365 dates；当地10–18点17,506 rows，其中fade=4,229（24.2%）。
- fade后仍在30/60/120分钟打穿当前档的比例为4.1%/8.0%/13.6%，EOD最终打穿21.7%；fade不是“升温结束”的同义词。
- fade后60分钟内转回fresh/plateau/pullback的比例为55.2%，说明它混合temporary dip与terminal fade。
- 两类fade的30分钟斜率中位数同为-0.8°C/h；但terminal/reheat的当地时刻中位为16.0/13.2点、距高点130/40分钟、辐射101/248 W/m²、forecast future margin -1.9/+0.4°C。因此单一fade标签丢失了决定是否reheat的交互。
- 当前first-positive：28笔，ROI=14.33%。固定等待10/20/30分钟的matched replay与CI见entry_delay_summary.csv；不从本窗挑最佳delay。
- 固定等10分钟ROI降至7.27%；等20分钟paired ROI delta=+0.04%（CI [-3.62%,+6.73%]）；没有证据支持统一延迟入场。
- first-positive中peak 0–1h为4笔/2错；pullback为7笔/0错、ROI=36.85%，但样本太小，只用于设计连续interaction。

## 口径

- 历史层只描述客观天气模式；市场层使用既有9个OOF日期、5-share ask+官方fee。
- 全部price行保留；无0.98策略阈值。delay缺盘口是evidence gap，单列coverage。
- 本报告用于设计nonlinear fade/reheat状态与预注册timing challenger，不改live。
