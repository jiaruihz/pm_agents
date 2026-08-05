# Helsinki signal quality upgrade review v1

Status: `fixed-row diagnostic / no threshold selected / no live change`

## 人话结论

- 0.99 附近不是坏数据；它在“胜率/笔数”研究摘要里属于低 materiality 统计尘埃：方向通常正确，但最大利润太小。完整概率分母、训练和最终策略均保留这些行。
- v7 primary 共 44 笔；其中 price≥0.98 有 15 笔，投入 $74.65、净赚 $0.35，ROI 0.47%，且没有解释任何错单。
- 真正的 10 个错误里，pullback/fade 占 9 个，距 forecast peak 0–60m 占 7 个；这是概率过度自信问题。

## 三种扩信号办法的实测

- 把 E3 current-X YES / E4 X+1 YES 也放进 first-positive router：60 笔、ROI 4.59%；E3/E4 各自为负，不应靠扩表达增加信号。
- 连续两次正 edge 才入场：41 笔、ROI 6.67%；连续三次为22 笔、ROI 2.06%。确认机制没有修好错误。
- market-anchor 固定敏感度中，25% weather residual 为 22 笔、ROI 24.54%，但 date bootstrap CI [-10.66%, 59.12%]，只能作为下一版预注册候选。
- 真正训练的 expanding compact residual 在后9日 Brier=0.15979，同期 market=0.07673；当前样本下明显失败。

## 正确升级方向

1. 信号定义拆成 probability signal 与研究摘要：raw edge 连续记录；研究时另报 execution materiality cohort（最小可实现美元利润/最大资本回报），只防止高胜率低收益行误导模型评价，不作为最终策略 price cap。
2. 错误修复改为 market-anchored residual：以 market logit 为 offset，在 date-X/state-entry grain 学模型相对市场的增量，重点建 pullback、peak clock、forecast revision、METAR age/basis 的 interaction。当前只有14个盘口日期，expanding logistic 明显劣于 market，不能冻结新 head。
3. 真正增加信号先补 evidence coverage：2,115 weather checkpoints 只有534行有 PIT book；扩大 targeted full-ladder/first-seen 覆盖和日期，比放宽表达更可靠。
4. position state 继续作为通用能力：按 date-X inventory 管理首次入场、持有、概率更新与退出；不要把同一物理观点的重复10分钟 rows 当成新独立信号。

## 资格

- 保持 research/collector；不改 live，不把本窗口的 0.98/0.95 切片变成 gate。
- 7/31+ frozen forward untouched；下一版必须先预注册 materiality 和 residual head，再在新日期验证。
