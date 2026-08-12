# 高价 NO 残值收割 v2:物理确认分层(中文报告)

状态:`snapshot`;仅研究,不改 live/shadow。

## 问题定义
前置 broad 基线证明:只按价格(NO ask>=0.85)无差别收残值,taker 持有到结算显著为负(edge>=0 篮子 ROI -2.0%,CI [-3.5%,-0.1%]),市场平均残值 5.4c 小于真实 exact-failure 6.5%。
v2 检验真正的论点:**carry 只有在 PIT 物理状态确认当天升温已结束时才能赚**——预报峰值时刻已过、预报天花板打不到目标档、温度路径回落/平台且高点已陈旧;并检查城市是否有稳定模式。

## 数据与口径
- paper snapshot 扫描:3676 个;原始 NO 腿行:21210;可执行(ask>=0.85、深度>=5.0 股):6882。
- 决策分母 = 每个 paper snapshot 轮询周期;candidate 字段只来自该行本身 + 同城同日更早快照的 PIT 累积(running-max 陈旧度、1h 趋势、METAR 节奏)。结算 label 在候选生成后 join。
- 物理分数 = canonical `weather_data_feed.weather_context.heating_done_features`(峰值时钟/预报余量/回落/高点陈旧度/1h 趋势),d2-d4 叠加 leg 级天花板余量 addon(沿用 heating_done_v1 逻辑扩到 d4)。**分数不含任何价格信息。**
- 帧覆盖 2026-06-20..2026-07-07(paper snapshot 的 METAR 字段 ~06-29 才有,更早无决策帧);只用结算完整日(>=30 城)。
- taker 成本 = ask + 0.05*p*(1-p) 手续费;first-cross 去重(city/date/leg/bracket 首次满足入场)。

## 已吸收的 broad v1 证据
本报告是 high-price NO residual/decay 家族的单一历史正本。原 broad v1
报告已合并删除，完整旧表仍可从 git history 恢复；以下保留后续判断真正需要的分母、反例和
overlay 结论。

- broad 帧为 3,676 个 paper snapshots、21,210 条 raw NO leg、6,882 条
  `ask>=0.85 & depth>=5` 可执行行和 2,504 条 first-cross，覆盖
  2026-06-20..07-07；expanding score 有 2,478 行。
- 0.85–0.90 带在 current/d1/d2/d3/d4 的 forward exact-failure 分别为
  15.2%/15.1%/13.8%/16.7%/14.5%，而平均残值约 12–12.6c；五腿持有
  ROI 全负（-2.3% 到 -5.4%）。这不是 fee 主导，而是 tail 被低估。
- 该价带 d1–d4 虽有 52%–64% 的行后来出现 `bid>=entry+5c`，hold 仍全负；
  事后最优退出收益只约 +2c 到 +4c，无法覆盖失败时约 -88c 的尾部损失，故
  “早段 decay”只是 mark-to-market illusion。
- empirical `p_hat_exact_failure` 的最低风险桶（<=2%）仍有 3.5% 实现失败，
  高于平均 2.1c market residual。forward `edge_hat>=0/0.5c/1c/2c` 篮子
  ROI 分别为 -2.0%/-3.1%/-3.5%/-3.0%，阈值越严没有修复 broad edge。
- 唯一小 pocket 是 d1 `ask>=0.95`、下一份 METAR<=5 分钟且 at-high：25 行、
  0 败、ROI +2.3% CI `[+1.9%,+2.7%]`；d3 0.90–0.95 与 current 0.99+
  也只有薄正点估。它们均为同窗事后切片，只能作为 shadow hypothesis。
- 25 个 live_real 亏损 city-day 中 17 个有同日 confident residual 行，但
  Helsinki 07-04、Busan 07-04/07-07 的 residual leg 本身发生更大 tail loss；
  因此覆盖率不能冒充 portfolio uplift，负 EV overlay 不用于回本。

## 政策阶梯(物理确认逐层收紧)
- `P0_all`:全部可执行高价 NO(v1 的 broad 基线)。
- `P1_heating_done`:leg 物理分数 >= 0.70 且路径 at_high/decline。
- `P2_done_plus_ceiling`:P1 且天花板确认(d2-d4 要求预报最高 < 目标档下沿至少 1 step;current/d1 要求预报不再高于当前 running high)。
- `P3_done_ceiling_peak_passed`:P2 且预报峰值已过 >= 1 小时。
- `P4_p3_plus_d1_obs_gate`:P3 且 d1 额外要求(下一份 METAR<=5 分钟 或 running high 已陈旧>=60 分钟)。

## 政策阶梯总表
| entry_policy | rows | settled_rows | active_dates | cities | avg_ask | market_residual | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0_all | 1774 | 1735 | 18 | 46 | 0.947 | 0.053 | 0.069 | 0.931 | -0.020 | -0.041 | -0.004 |
| P1_heating_done | 276 | 271 | 16 | 36 | 0.957 | 0.043 | 0.055 | 0.945 | -0.015 | -0.039 | 0.002 |
| P2_done_plus_ceiling | 263 | 259 | 16 | 35 | 0.958 | 0.042 | 0.046 | 0.954 | -0.006 | -0.032 | 0.013 |
| P3_done_ceiling_peak_passed | 132 | 132 | 16 | 31 | 0.960 | 0.040 | 0.038 | 0.962 | 0.000 | -0.037 | 0.024 |
| P4_p3_plus_d1_obs_gate | 125 | 125 | 16 | 31 | 0.960 | 0.040 | 0.040 | 0.960 | -0.001 | -0.042 | 0.023 |

## 分腿
| entry_policy | leg | rows | settled_rows | active_dates | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0_all | current_no | 262 | 259 | 8 | 0.062 | 0.938 | -0.017 | -0.046 | 0.008 |
| P0_all | d1_no | 444 | 438 | 18 | 0.080 | 0.920 | -0.026 | -0.045 | -0.012 |
| P0_all | d2_no | 395 | 388 | 10 | 0.072 | 0.928 | -0.020 | -0.048 | 0.004 |
| P0_all | d3_no | 355 | 343 | 10 | 0.058 | 0.942 | -0.014 | -0.050 | 0.019 |
| P0_all | d4_no | 318 | 307 | 11 | 0.068 | 0.932 | -0.022 | -0.061 | 0.009 |
| P1_heating_done | current_no | 6 | 6 | 4 | 0.167 | 0.833 | -0.093 | -0.728 | 0.108 |
| P1_heating_done | d1_no | 92 | 91 | 16 | 0.088 | 0.912 | -0.040 | -0.099 | 0.006 |
| P1_heating_done | d2_no | 105 | 104 | 8 | 0.038 | 0.962 | 0.003 | -0.018 | 0.029 |
| P1_heating_done | d3_no | 54 | 52 | 7 | 0.038 | 0.962 | -0.012 | -0.047 | 0.030 |
| P1_heating_done | d4_no | 19 | 18 | 5 | 0.000 | 1.000 | 0.024 | 0.012 | 0.047 |
| P2_done_plus_ceiling | current_no | 6 | 6 | 4 | 0.167 | 0.833 | -0.093 | -0.728 | 0.108 |
| P2_done_plus_ceiling | d1_no | 80 | 80 | 16 | 0.062 | 0.938 | -0.015 | -0.085 | 0.038 |
| P2_done_plus_ceiling | d2_no | 104 | 103 | 8 | 0.039 | 0.961 | 0.003 | -0.019 | 0.029 |
| P2_done_plus_ceiling | d3_no | 54 | 52 | 7 | 0.038 | 0.962 | -0.012 | -0.047 | 0.030 |
| P2_done_plus_ceiling | d4_no | 19 | 18 | 5 | 0.000 | 1.000 | 0.024 | 0.012 | 0.047 |
| P3_done_ceiling_peak_passed | d1_no | 69 | 69 | 16 | 0.058 | 0.942 | -0.013 | -0.078 | 0.032 |
| P3_done_ceiling_peak_passed | d2_no | 42 | 42 | 8 | 0.024 | 0.976 | 0.005 | -0.038 | 0.035 |
| P3_done_ceiling_peak_passed | d3_no | 15 | 15 | 5 | 0.000 | 1.000 | 0.025 | 0.009 | 0.044 |
| P4_p3_plus_d1_obs_gate | d1_no | 62 | 62 | 16 | 0.065 | 0.935 | -0.017 | -0.092 | 0.034 |
| P4_p3_plus_d1_obs_gate | d2_no | 42 | 42 | 8 | 0.024 | 0.976 | 0.005 | -0.038 | 0.035 |
| P4_p3_plus_d1_obs_gate | d3_no | 15 | 15 | 5 | 0.000 | 1.000 | 0.025 | 0.009 | 0.044 |

## 分价带
| entry_policy | price_band | rows | settled_rows | active_dates | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P0_all | 85_90 | 372 | 361 | 15 | 0.172 | 0.828 | -0.060 | -0.110 | -0.020 |
| P0_all | 90_95 | 415 | 403 | 17 | 0.087 | 0.913 | -0.022 | -0.092 | 0.025 |
| P0_all | 95_99 | 722 | 708 | 17 | 0.032 | 0.968 | -0.011 | -0.026 | 0.006 |
| P0_all | 99_plus | 265 | 263 | 14 | 0.000 | 1.000 | 0.006 | 0.006 | 0.006 |
| P1_heating_done | 85_90 | 37 | 37 | 10 | 0.135 | 0.865 | -0.018 | -0.124 | 0.078 |
| P1_heating_done | 90_95 | 55 | 51 | 12 | 0.098 | 0.902 | -0.036 | -0.164 | 0.046 |
| P1_heating_done | 95_99 | 140 | 139 | 11 | 0.036 | 0.964 | -0.013 | -0.049 | 0.020 |
| P1_heating_done | 99_plus | 44 | 44 | 7 | 0.000 | 1.000 | 0.006 | 0.006 | 0.007 |
| P2_done_plus_ceiling | 85_90 | 35 | 35 | 10 | 0.114 | 0.886 | 0.006 | -0.100 | 0.124 |
| P2_done_plus_ceiling | 90_95 | 51 | 47 | 11 | 0.064 | 0.936 | -0.001 | -0.091 | 0.064 |
| P2_done_plus_ceiling | 95_99 | 134 | 134 | 11 | 0.037 | 0.963 | -0.015 | -0.051 | 0.020 |
| P2_done_plus_ceiling | 99_plus | 43 | 43 | 7 | 0.000 | 1.000 | 0.006 | 0.006 | 0.007 |
| P3_done_ceiling_peak_passed | 85_90 | 19 | 19 | 10 | 0.105 | 0.895 | 0.010 | -0.134 | 0.122 |
| P3_done_ceiling_peak_passed | 90_95 | 22 | 22 | 9 | 0.045 | 0.955 | 0.019 | -0.137 | 0.072 |
| P3_done_ceiling_peak_passed | 95_99 | 64 | 64 | 11 | 0.031 | 0.969 | -0.011 | -0.036 | 0.022 |
| P3_done_ceiling_peak_passed | 99_plus | 27 | 27 | 8 | 0.000 | 1.000 | 0.006 | 0.005 | 0.006 |
| P4_p3_plus_d1_obs_gate | 85_90 | 19 | 19 | 10 | 0.105 | 0.895 | 0.010 | -0.134 | 0.122 |
| P4_p3_plus_d1_obs_gate | 90_95 | 20 | 20 | 9 | 0.050 | 0.950 | 0.016 | -0.163 | 0.073 |
| P4_p3_plus_d1_obs_gate | 95_99 | 61 | 61 | 11 | 0.033 | 0.967 | -0.013 | -0.038 | 0.022 |
| P4_p3_plus_d1_obs_gate | 99_plus | 25 | 25 | 7 | 0.000 | 1.000 | 0.006 | 0.005 | 0.006 |

## 物理分数单调性(broad 帧,按分数桶)
| hd_bucket | rows | settled_rows | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| not_done | 1241 | 1217 | 0.068 | 0.932 | -0.019 | -0.042 | 0.003 |
| marginal | 387 | 376 | 0.072 | 0.928 | -0.023 | -0.050 | -0.005 |
| probable | 54 | 52 | 0.077 | 0.923 | -0.030 | -0.123 | 0.035 |
| confirmed | 92 | 90 | 0.067 | 0.933 | -0.012 | -0.097 | 0.036 |

| leg | hd_bucket | rows | settled_rows | exact_failure_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_no | not_done | 257 | 254 | 0.059 | -0.014 | -0.049 | 0.011 |
| d1_no | not_done | 378 | 374 | 0.072 | -0.021 | -0.036 | -0.005 |
| d1_no | marginal | 14 | 14 | 0.143 | -0.050 | -0.328 | 0.111 |
| d1_no | probable | 13 | 11 | 0.182 | -0.109 | -0.302 | 0.082 |
| d1_no | confirmed | 39 | 39 | 0.103 | -0.038 | -0.200 | 0.049 |
| d2_no | not_done | 246 | 241 | 0.066 | -0.016 | -0.055 | 0.024 |
| d2_no | marginal | 90 | 88 | 0.091 | -0.030 | -0.086 | 0.009 |
| d2_no | probable | 26 | 26 | 0.077 | -0.037 | -0.211 | 0.051 |
| d2_no | confirmed | 33 | 33 | 0.061 | -0.005 | -0.066 | 0.060 |
| d3_no | not_done | 200 | 194 | 0.062 | -0.015 | -0.066 | 0.020 |
| d3_no | marginal | 127 | 122 | 0.066 | -0.022 | -0.057 | 0.011 |
| d3_no | probable | 12 | 12 | 0.000 | 0.034 | 0.021 | 0.055 |
| d3_no | confirmed | 16 | 15 | 0.000 | 0.030 | 0.018 | 0.051 |
| d4_no | not_done | 160 | 154 | 0.084 | -0.032 | -0.087 | 0.022 |
| d4_no | marginal | 154 | 150 | 0.053 | -0.013 | -0.068 | 0.012 |

## P3 分城市
| city | rows | settled_rows | active_dates | avg_ask | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Amsterdam | 13 | 13 | 9 | 0.962 | 0.000 | 1.000 | 0.037 | 0.021 | 0.059 |
| Ankara | 15 | 15 | 11 | 0.951 | 0.133 | 0.867 | -0.091 | -0.288 | 0.050 |
| Busan | 6 | 6 | 4 | 0.978 | 0.000 | 1.000 | 0.022 | 0.006 | 0.037 |
| CapeTown | 4 | 4 | 3 | 0.968 | 0.000 | 1.000 | 0.032 | 0.010 | 0.055 |
| Chengdu | 6 | 6 | 2 | 0.965 | 0.000 | 1.000 | 0.035 | 0.025 | 0.055 |
| Chongqing | 6 | 6 | 3 | 0.964 | 0.000 | 1.000 | 0.035 | 0.005 | 0.066 |
| Guangzhou | 9 | 9 | 5 | 0.938 | 0.111 | 0.889 | -0.055 | -0.311 | 0.097 |
| Helsinki | 4 | 4 | 2 | 0.963 | 0.000 | 1.000 | 0.037 | 0.036 | 0.040 |
| HongKong | 10 | 10 | 3 | 0.956 | 0.100 | 0.900 | -0.061 | -0.105 | 0.050 |
| Istanbul | 7 | 7 | 5 | 0.973 | 0.000 | 1.000 | 0.026 | 0.010 | 0.048 |
| KualaLumpur | 5 | 5 | 4 | 0.981 | 0.000 | 1.000 | 0.018 | 0.006 | 0.030 |
| Lucknow | 4 | 4 | 2 | 0.932 | 0.000 | 1.000 | 0.070 | 0.005 | 0.093 |
| Moscow | 5 | 5 | 3 | 0.967 | 0.000 | 1.000 | 0.033 | 0.012 | 0.042 |
| Munich | 7 | 7 | 3 | 0.982 | 0.000 | 1.000 | 0.017 | 0.008 | 0.020 |
| Shanghai | 4 | 4 | 2 | 0.945 | 0.250 | 0.750 | -0.208 | -1.000 | 0.064 |

## P3 城市前后半窗稳定性(H1<2026-07-03<=H2)
| city | roi_H1 | roi_H2 | same_sign |
| --- | --- | --- | --- |
| Amsterdam | 0.045 | 0.029 | True |
| Ankara | -0.206 | 0.039 | False |
| Busan | 0.014 | 0.037 | True |
| Chongqing | 0.066 | 0.021 | True |
| Guangzhou | -0.322 | 0.088 | False |
| HongKong | 0.041 | -0.105 | False |
| Istanbul | 0.047 | 0.012 | True |
| KualaLumpur | 0.010 | 0.023 | True |
| Moscow | 0.035 | 0.031 | True |
| Munich | 0.020 | 0.009 | True |

## P4 分腿
| leg | rows | settled_rows | active_dates | exact_failure_rate | hit_rate | roi | roi_ci_low | roi_ci_high |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| current_no | 4 | 4 | 3 | 0.000 | 1.000 | 0.072 | 0.025 | 0.099 |
| d1_no | 62 | 62 | 16 | 0.065 | 0.935 | -0.017 | -0.092 | 0.034 |
| d2_no | 42 | 42 | 8 | 0.024 | 0.976 | 0.005 | -0.038 | 0.035 |
| d3_no | 15 | 15 | 5 | 0.000 | 1.000 | 0.025 | 0.009 | 0.044 |

## P3 失败案例
| city | target_date | ts_beijing | leg | bracket | entry_price | leg_residual_done_score | ceiling_margin_steps | forecast_peak_delta_hours_local | minutes_since_running_max | path_state | pnl_per_share |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Ankara | 2026-06-21 | 2026-06-21 21:44:37 | d1_no | 26 | 0.850 | 0.740 | 3.800 | 1.730 | 109.017 | at_high | -0.856 |
| Ankara | 2026-06-27 | 2026-06-27 21:36:02 | d1_no | 28 | 0.950 | 0.960 | 4.000 | 1.600 | 143.767 | decline | -0.952 |
| Shanghai | 2026-06-30 | 2026-06-30 16:02:37 | d1_no | 28 | 0.969 | 0.810 | 2.400 | 2.030 | 73.167 | at_high | -0.970 |
| Guangzhou | 2026-07-01 | 2026-07-01 15:56:37 | d2_no | 34 | 0.990 | 1.000 | 1.800 | 2.930 | 376.800 | decline | -0.990 |
| HongKong | 2026-07-06 | 2026-07-06 10:31:24 | d1_no | 31 | 0.890 | 0.880 | 5.600 | 6.520 | 150.950 | at_high | -0.895 |

## 结论
1. **方向验证成立:物理确认逐层收紧,tail 单调收敛。** exact-failure 从 P0 6.9% → P1 5.5% → P2 4.6% → P3 3.8%,ROI 从 -2.0% 收敛到 0.0%。"最高温已过 + 气候条件不支持升温才能 carry" 的判断在数据上是对的——不确认就买高价 NO 是显著负 EV。
2. **但 broad 层面只到打平,不到正 carry:市场也在为同样的确认信息提价。** P3 平均 ask 0.960、残值 4.0c ≈ 实际失败率 3.8%——确认后的残值是市场对剩余 tail 的公允定价,taker 吃不到剩余的 4c。想赚必须比市场**更早**(P2/P3 在 85-90、90-95 带点估转正:+0.6%~+1.9%,CI 仍跨 0)或**更准**(下面第 3 点),或走 maker。
3. **正 pocket 在"远腿 + 天花板确认"**:d3 分数 probable/confirmed 0 失败、ROI +3.0%~+3.4%(CI 为正);P3 的 d3(15 行 0 败 +2.5%)、P1/P2 的 d4(18 行 0 败 +2.4%)。物理逻辑:目标档在预报天花板之上 >=1-2 step 时,exact-failure 需要预报大错才会发生,这是真正被物理压住的 tail。
4. **d1 one-tick 即使确认后仍是坏腿**:P3 下 d1 失败率 5.8%、ROI -1.3%;broad 帧分数 confirmed 桶的 d1 失败率反而 10.3%。一档之差随时被一份 METAR 打穿,天花板逻辑对它不起作用(margin 太小),P4 的 obs gate 也救不回。**残值策略应默认排除 d1(机制边界,不是补丁 gate)。**
5. **城市模式存在且机制可解释**:P3 失败全部集中在 Ankara/上海/广州/香港——华南沿海午后对流放晴 reheat、热带城市 forecast peak clock 失真(HK 案例 10:31 却显示"峰值已过 6.5h")、Ankara 是已知 forecast 失准城市(2026-07-08 enrichment:ICON 显著优于现用模型)。零失败城市组(Amsterdam/Busan/Chengdu/Chongqing/Istanbul/KualaLumpur/Moscow/Munich 等)前后半窗 ROI 同号为正(10 城中 7 城稳定)。这是 reheat 气候型 vs 稳定气候型的机制差异,不是白名单拟合,但样本小,只能 shadow 验证。
6. **交易动作**:不改 live。下一步是把 `P3 & leg∈(d2,d3,d4) & 排除 reheat 型城市(华南沿海/热带 peak-clock 失真/已知 forecast 失准城)` 预注册成 shadow 口径收 forward,同时评估 maker 挂单经济性(v1 系列 maker 面板为正但排队现实未验证)。0.85-0.90 早段 decay 只在 P2+ 确认下点估转正、样本太小,随 shadow 一起累积。

## Contract Gates
- 显著性门:broad P0 显著为负(继承 v1);P3 整体 CI [-3.7%,+2.4%] 跨 0;正 pocket(d3/d4 确认层)CI 为正但行数 15-19、日数 5-7,支撑不足。=> 对正结论 FAIL。
- 基准门:P3 相对同价位无脑买 NO(P0)失败率 -3.1pp、ROI +2pp,方向性超额存在;但正 ROI 本身未显著跨 0。=> FAIL(方向性 PASS)。
- 前瞻门:物理分数是 canonical 预定义(`weather_data_feed.weather_context`,非本窗调参),政策阶梯是预声明单调收紧而非扫参;但城市切片是事后观察,帧仅 2026-06-20..07-07。=> FAIL,需预注册 shadow 收 forward。
- 结论分级:broad taker carry `rejected`(继承 v1);P3 远腿 + 城市排除 = `shadow_candidate`;d1 排除是机制边界。不改 live。
- 数据说明:结算完整日到 2026-07-07;taker 口径 top-of-book + 5 股深度;maker 未测;帧内 06-29 前行数极少(METAR 字段覆盖)。
