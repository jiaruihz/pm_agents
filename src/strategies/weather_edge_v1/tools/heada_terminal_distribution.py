"""HeadA terminal tmax distribution kernel (parameter-free, PIT-bias based).

第一性原理：exact-bracket tmax market 的 YES-X 公允价 = P(终值最高温 ∈ bracket X)。
终值 tmax ≈ forecast_max + bias，其中 bias 有 as-of（PIT）经验分布，由 tail telemetry
产出的分位数刻画：bias_p50（中位）、bias_p90（上分位）、bias_mean（均值，定偏度）。

用 two-piece（split）normal 把这些矩映射成每档 exact 概率——**无拟合自由参数**，
分位数本身就是 PIT 的，因此不存在过拟合，与 canonical coherent calibrator「无 city/source
自由参数」同哲学。三态自然分解：
    P(below)    = 没够到该档（bias 下尾）
    P(win exact)= 正好落在该档
    P(overshoot)= 冲过到更高档（bias 上尾）——exact-bracket 特有的 overshoot 风险。

源质量（source_quality）**只用于缩 sizing，不改概率**——尊重既有研究
（source-quality-score-v2 / source-risk-monotonic）的裁决：low source 只 `do_not_size_up`，
不是 hard block、不是概率移位。

用法（每个候选档一行 feature dict）：
    dist = terminal_bracket_distribution(row)
    p_yes = dist["p_win_exact"]        # 该 exact 档公允价
    # 对 canonical 粗桶归一 / 对 book 算真边 / Kelly sizing 见 research 脚本
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

# 标准正态 90 分位 z 值：Φ^{-1}(0.90)
_Z90 = 1.2815515594465004
_SQRT_HALF_PI = math.sqrt(math.pi / 2.0)
_SQRT2 = math.sqrt(2.0)

# 单位换算：bracket_width_f 已是 F；bias 分位数经数据验证也是 F（见 v1 research）。
# σ_u 下限：避免 p90≈p50 时方差塌成 0 导致概率退化成 0/1。
_MIN_SIGMA_F = 0.35
# σ_l 相对 σ_u 的夹逼，防止偏度矩把下尾压得过窄/过宽。
_SIGMA_L_FLOOR_RATIO = 0.35
_SIGMA_L_CEIL_RATIO = 1.75


def _to_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def split_normal_cdf(x: float, sigma_lo: float, sigma_hi: float) -> float:
    """Two-piece (split) normal CDF, median at 0, lower scale sigma_lo, upper sigma_hi.

    连续且在 0 处 pdf 连续；退化 sigma_lo==sigma_hi 时回到普通正态。
    """
    if sigma_lo <= 0 or sigma_hi <= 0:
        # 退化：当作阶跃，x>=0 记 1
        return 1.0 if x >= 0 else 0.0
    denom = sigma_lo + sigma_hi
    if x < 0:
        return (2.0 * sigma_lo / denom) * _std_normal_cdf(x / sigma_lo)
    return (sigma_lo / denom) + (2.0 * sigma_hi / denom) * (_std_normal_cdf(x / sigma_hi) - 0.5)


@dataclass(frozen=True)
class TerminalBracketDistribution:
    """单个 exact bracket 的终值分布分解（概率均基于 bias PIT 矩，无拟合参数）。"""

    p_below: float          # P(终值 < bracket_low)：没够到
    p_win_exact: float      # P(终值 ∈ [bracket_low, bracket_high))：正好命中
    p_overshoot: float      # P(终值 >= bracket_high)：冲过
    sigma_lo_f: float       # 采用的下尾尺度（F）
    sigma_hi_f: float       # 采用的上尾尺度（F）
    z_low_f: float          # bracket_low 相对 bias 校正中位的偏移（F）
    z_high_f: float         # bracket_high 相对 bias 校正中位的偏移（F）
    degraded: bool          # True = 输入缺失，回退到 median 点估近似
    reason: str

    def as_dict(self) -> dict[str, float | bool | str]:
        return {
            "p_below": round(self.p_below, 6),
            "p_win_exact": round(self.p_win_exact, 6),
            "p_overshoot": round(self.p_overshoot, 6),
            "sigma_lo_f": round(self.sigma_lo_f, 4),
            "sigma_hi_f": round(self.sigma_hi_f, 4),
            "z_low_f": round(self.z_low_f, 4),
            "z_high_f": round(self.z_high_f, 4),
            "degraded": self.degraded,
            "reason": self.reason,
        }


def _resolve_geometry(row: Mapping[str, Any]) -> Optional[tuple[float, float, float]]:
    """返回 (z_low_f, width_f, bias_p50_f)：bracket_low 相对 forecast 的原始距离(F)、档宽、中位 bias。

    优先用预算的 raw_dist_br（= (bracket_low_f - forecast_max_f)/width_f），避开
    forecast_max_f 的大面积缺失。
    """
    width_f = _to_float(row.get("bracket_width_f"))
    bias_p50 = _to_float(row.get("bias_p50_asof"), _to_float(row.get("bias_p50")))
    if not (math.isfinite(width_f) and width_f > 0 and math.isfinite(bias_p50)):
        return None
    raw_dist_br = _to_float(row.get("raw_dist_br"))
    if math.isfinite(raw_dist_br):
        dist_low_f = raw_dist_br * width_f  # bracket_low - forecast_max，单位 F
        return dist_low_f, width_f, bias_p50
    # 回退：直接用 forecast_max_f 与 bracket_low_f
    fmax = _to_float(row.get("forecast_max_f_used"), _to_float(row.get("forecast_max_f")))
    blow = _to_float(row.get("bracket_low_f"))
    if math.isfinite(fmax) and math.isfinite(blow):
        return blow - fmax, width_f, bias_p50
    return None


def terminal_bracket_distribution(row: Mapping[str, Any]) -> TerminalBracketDistribution:
    """从 PIT bias 矩构造单档终值分布（below / win / overshoot），无拟合参数。"""
    geom = _resolve_geometry(row)
    if geom is None:
        return TerminalBracketDistribution(
            p_below=math.nan, p_win_exact=math.nan, p_overshoot=math.nan,
            sigma_lo_f=math.nan, sigma_hi_f=math.nan, z_low_f=math.nan, z_high_f=math.nan,
            degraded=True, reason="missing_geometry_or_bias",
        )
    dist_low_f, width_f, bias_p50 = geom

    # bracket 边界相对「bias 校正后的 forecast 中位」的偏移（median 移到 0）。
    z_low = dist_low_f - bias_p50
    z_high = z_low + width_f

    bias_p90 = _to_float(row.get("bias_p90_asof"), _to_float(row.get("bias_p90")))
    bias_mean = _to_float(row.get("bias_mean_asof"), _to_float(row.get("bias_mean")))

    # 上尾尺度：从 (p90 - p50) 反推 σ_u。缺 p90 则回退到点估阶跃。
    if math.isfinite(bias_p90) and (bias_p90 - bias_p50) > 0:
        sigma_hi = max(_MIN_SIGMA_F, (bias_p90 - bias_p50) / _Z90)
    else:
        p_win = 1.0 if (z_low <= 0.0 < z_high) else 0.0
        p_below = 1.0 if z_high <= 0.0 else 0.0
        return TerminalBracketDistribution(
            p_below=p_below, p_win_exact=p_win, p_overshoot=1.0 - p_below - p_win,
            sigma_lo_f=math.nan, sigma_hi_f=math.nan, z_low_f=z_low, z_high_f=z_high,
            degraded=True, reason="missing_p90_point_estimate",
        )

    # 下尾尺度：用 split-normal 均值约束 mean = μ + (σ_hi - σ_lo)·sqrt(2/π)，μ=p50=0。
    # → σ_lo = σ_hi - (bias_mean - bias_p50)·sqrt(π/2)，再夹逼到合理带内。
    if math.isfinite(bias_mean):
        sigma_lo = sigma_hi - (bias_mean - bias_p50) * _SQRT_HALF_PI
    else:
        sigma_lo = sigma_hi
    sigma_lo = min(max(sigma_lo, _SIGMA_L_FLOOR_RATIO * sigma_hi), _SIGMA_L_CEIL_RATIO * sigma_hi)
    sigma_lo = max(sigma_lo, _MIN_SIGMA_F)

    cdf_low = split_normal_cdf(z_low, sigma_lo, sigma_hi)
    cdf_high = split_normal_cdf(z_high, sigma_lo, sigma_hi)
    p_below = cdf_low
    p_win = max(0.0, cdf_high - cdf_low)
    p_overshoot = max(0.0, 1.0 - cdf_high)

    return TerminalBracketDistribution(
        p_below=p_below, p_win_exact=p_win, p_overshoot=p_overshoot,
        sigma_lo_f=sigma_lo, sigma_hi_f=sigma_hi, z_low_f=z_low, z_high_f=z_high,
        degraded=False, reason="ok",
    )


# ---- 源质量 → sizing 信心（不改概率）----------------------------------------

def source_confidence_multiplier(row: Mapping[str, Any]) -> float:
    """把 source_quality tier 映射成 sizing 乘子（∈[0,1.5]）。

    尊重研究裁决：low 只降权不 hard block。默认读既有 `source_quality_tier_v1`。
    """
    tier = str(row.get("source_quality_tier_v1") or row.get("source_quality_tier") or "").lower()
    return {
        "high": 1.5,
        "mid": 1.2,
        "neutral": 1.0,
        "low": 0.6,       # do_not_size_up，但不为 0（研究：low 桶含 winner）
        "extreme": 0.4,
    }.get(tier, 1.0)


def kelly_fraction(p_win: float, price: float, *, cap: float = 0.5) -> float:
    """binary 合约 Kelly 分数：赢赔 (1-price)/price，输赔 1。返回 [0, cap]。"""
    if not (0.0 < price < 1.0) or not (0.0 <= p_win <= 1.0):
        return 0.0
    b = (1.0 - price) / price
    f = (p_win * b - (1.0 - p_win)) / b
    return max(0.0, min(cap, f))
