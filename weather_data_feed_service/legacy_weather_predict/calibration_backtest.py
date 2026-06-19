"""
校准验证脚本 — 验证概率模型准确性

用 v5 历史数据（GFS 预报 vs WU 实测）验证经验分布概率模型：
1. Brier Score（越低越好，完美=0，随机=0.25）
2. Calibration Curve（斜率应接近 1）
3. Reliability Diagram（可视化）
4. 按城市/月份分组的表现

目标：Brier score < 0.15 即可接受。

运行方式：
    python3 calibration_backtest.py
"""

import json
import numpy as np
import os
from pathlib import Path
from datetime import datetime, timedelta

# ─── 配置 ──────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DEFAULT_RUNTIME_DIR = BASE_DIR.parent / "runtime"
CACHE_DIR = str(Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache")))
V5_JSON = "calibration_results_v5.json"

# Polymarket 典型盘口阈值范围（相对于预报均值的偏移，°F）
THRESHOLD_OFFSETS = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5]

# ─── 工具函数 ───────────────────────────────────────────────────────────────

def _forecast_cache_score(filename):
    """Prefer forecast caches with more hourly rows, then newer mtime."""
    path = os.path.join(CACHE_DIR, filename)
    try:
        with open(path) as f:
            raw = json.load(f)
        n = len(raw.get("hourly", {}).get("time", []))
    except Exception:
        n = 0
    return (n, os.path.getmtime(path), filename)


def _select_forecast_cache(prefixes, city):
    matches = []
    for prefix in prefixes:
        matches.extend(
            f for f in os.listdir(CACHE_DIR)
            if f.startswith(f"{prefix}{city}_") and f.endswith(".json")
        )
    if not matches:
        return None
    return max(matches, key=_forecast_cache_score)

def load_wu_obs(icao):
    """加载 WU 逐小时观测，计算日最高温（当地时间切日）"""
    path = os.path.join(CACHE_DIR, "wu_obs", f"wu_obs_{icao}.csv")
    if not os.path.exists(path):
        print(f"  [WARN] 缺少 WU 数据: {path}")
        return {}

    daily_max = {}
    with open(path) as f:
        lines = f.readlines()

    if not lines:
        return {}

    header = lines[0].strip().split(",")
    try:
        idx_date = header.index("date_local")
        idx_temp = header.index("temp")
    except ValueError:
        print(f"  [WARN] CSV 格式错误: {path}")
        return {}

    for line in lines[1:]:
        parts = line.strip().split(",")
        if len(parts) <= max(idx_date, idx_temp):
            continue
        date_str = parts[idx_date].strip()
        temp_str = parts[idx_temp].strip()
        if not date_str or not temp_str:
            continue
        try:
            t = float(temp_str)
            if date_str not in daily_max or t > daily_max[date_str]:
                daily_max[date_str] = t
        except ValueError:
            continue

    return daily_max


def load_gfs_daily(city, utc_offset):
    """加载 GFS 365天历史预报，计算当地时间日最高温"""
    match = _select_forecast_cache(["gfs_365d_", "gfs_v4_"], city)
    if not match:
        print(f"  [WARN] 缺少 GFS 缓存: {city}")
        return {}

    path = os.path.join(CACHE_DIR, match)
    with open(path) as f:
        raw = json.load(f)

    # 格式：{"hourly": {"time": [...], "temperature_2m": [...]}}
    if "hourly" not in raw:
        return {}

    times = raw["hourly"].get("time", [])
    temps = raw["hourly"].get("temperature_2m", [])

    if not times or not temps:
        return {}

    daily_max = {}
    for t_str, temp_c in zip(times, temps):
        if temp_c is None:
            continue
        try:
            dt_utc = datetime.fromisoformat(t_str)
        except ValueError:
            continue

        # 转换为当地时间
        from datetime import timedelta
        dt_local = dt_utc + timedelta(hours=utc_offset)
        date_str = dt_local.strftime("%Y-%m-%d")

        # GFS 缓存已是 °F（Open-Meteo 拉取时设置了 &temperature_unit=fahrenheit）
        temp_f = temp_c  # 变量名保留，实际已是 °F

        if date_str not in daily_max or temp_f > daily_max[date_str]:
            daily_max[date_str] = temp_f

    return daily_max


MODEL_PREFIXES = {
    "gfs": ["gfs_365d_", "gfs_v4_"],
    "ecmwf": ["ecmwf_v4_"],
    "icon_eu": ["icon_eu_v5_"],
    "jma": ["jma_v5_"],
    "hrrr": ["hrrr_v5_"],
}


def load_model_daily(city, utc_offset, model="gfs"):
    """通用模型加载器。model = gfs/ecmwf/icon_eu/jma/hrrr"""
    prefixes = MODEL_PREFIXES.get(model, MODEL_PREFIXES["gfs"])
    match = _select_forecast_cache(prefixes, city)
    if not match:
        return {}

    path = os.path.join(CACHE_DIR, match)
    with open(path) as f:
        raw = json.load(f)

    if "hourly" not in raw:
        return {}
    times = raw["hourly"].get("time", [])
    temps = raw["hourly"].get("temperature_2m", [])
    if not times or not temps:
        return {}

    daily_max = {}
    for t_str, temp_f in zip(times, temps):
        if temp_f is None:
            continue
        try:
            dt_utc = datetime.fromisoformat(t_str)
        except ValueError:
            continue
        dt_local = dt_utc + timedelta(hours=utc_offset)
        date_str = dt_local.strftime("%Y-%m-%d")
        if date_str not in daily_max or temp_f > daily_max[date_str]:
            daily_max[date_str] = temp_f
    return daily_max


def empirical_cdf(x, errors_sorted):
    """P(ε ≤ x)"""
    if not errors_sorted:
        return 0.5
    count = sum(1 for e in errors_sorted if e <= x)
    return count / len(errors_sorted)


def prob_wu_ge_x(gfs_forecast, threshold, errors_sorted):
    """P(WU ≥ threshold)，使用经验误差分布"""
    gap = threshold - gfs_forecast
    return max(0.0, min(1.0, 1.0 - empirical_cdf(gap, errors_sorted)))


def brier_score(p_list, outcome_list):
    """Brier Score = mean((p - outcome)^2)"""
    n = len(p_list)
    if n == 0:
        return None
    return sum((p - o) ** 2 for p, o in zip(p_list, outcome_list)) / n


def calibration_bins(p_list, outcome_list, n_bins=10):
    """
    校准曲线：将概率分成 n_bins 个区间，
    对比预测概率均值 vs 实际频率
    """
    bins = [[] for _ in range(n_bins)]
    for p, o in zip(p_list, outcome_list):
        bin_idx = min(int(p * n_bins), n_bins - 1)
        bins[bin_idx].append((p, o))

    results = []
    for i, bin_data in enumerate(bins):
        if not bin_data:
            continue
        pred_probs = [x[0] for x in bin_data]
        outcomes = [x[1] for x in bin_data]
        results.append({
            'bin_center': (i + 0.5) / n_bins,
            'mean_pred': np.mean(pred_probs),
            'actual_freq': np.mean(outcomes),
            'n': len(bin_data)
        })

    return results


# ─── 主验证逻辑 ────────────────────────────────────────────────────────────

def backtest_city(city, city_data):
    """对单个城市做回测验证"""
    icao = city_data['icao']
    utc_offset = city_data['utc_offset']
    region = city_data.get('region', 'US')

    print(f"\n{'='*60}")
    print(f"城市: {city} ({icao}, UTC{utc_offset:+d})")

    # 加载数据
    wu_daily = load_wu_obs(icao)
    gfs_daily = load_gfs_daily(city, utc_offset)

    if not wu_daily or not gfs_daily:
        print(f"  [SKIP] 数据不足")
        return None

    # 找共同日期
    common_dates = sorted(set(wu_daily.keys()) & set(gfs_daily.keys()))
    if len(common_dates) < 30:
        print(f"  [SKIP] 共同日期太少: {len(common_dates)} 天")
        return None

    print(f"  共同日期: {len(common_dates)} 天 ({common_dates[0]} ~ {common_dates[-1]})")

    # 计算误差序列
    errors = []
    dates_used = []
    for d in common_dates:
        wu_t = wu_daily[d]
        gfs_t = gfs_daily[d]
        if wu_t is None or gfs_t is None:
            continue
        errors.append(wu_t - gfs_t)  # WU_actual - GFS_forecast
        dates_used.append(d)

    if len(errors) < 30:
        print(f"  [SKIP] 有效误差数据不足: {len(errors)}")
        return None

    errors_sorted = sorted(errors)
    print(f"  误差统计: bias={np.mean(errors):.2f}°F, RMSE={np.sqrt(np.mean([e**2 for e in errors])):.2f}°F, n={len(errors)}")

    # 交叉验证：留一法（LOO-like，实际用月度交叉验证）
    all_probs = []
    all_outcomes = []
    all_thresholds = []

    # 按月分组
    monthly_groups = {}
    for d, e in zip(dates_used, errors):
        m = d[:7]  # "YYYY-MM"
        monthly_groups.setdefault(m, []).append((d, e))

    for month, group in monthly_groups.items():
        # 训练集 = 其他月份的误差
        train_errors = [e for m, g in monthly_groups.items() if m != month for _, e in g]
        if len(train_errors) < 15:
            continue
        train_errors_sorted = sorted(train_errors)

        for d, e in group:
            gfs_t = gfs_daily[d]
            wu_actual = wu_daily[d]

            # 对多个阈值做预测
            for offset in THRESHOLD_OFFSETS:
                threshold = round(gfs_t + offset)
                p = prob_wu_ge_x(gfs_t, threshold, train_errors_sorted)
                outcome = 1.0 if wu_actual >= threshold else 0.0
                all_probs.append(p)
                all_outcomes.append(outcome)
                all_thresholds.append(offset)

    if not all_probs:
        print(f"  [SKIP] 交叉验证数据不足")
        return None

    # 计算指标
    bs = brier_score(all_probs, all_outcomes)
    cal_bins = calibration_bins(all_probs, all_outcomes)

    # 校准斜率（理想斜率=1）
    mean_preds = [b['mean_pred'] for b in cal_bins if b['n'] >= 5]
    actual_freqs = [b['actual_freq'] for b in cal_bins if b['n'] >= 5]
    if len(mean_preds) >= 3:
        slope = np.polyfit(mean_preds, actual_freqs, 1)[0]
    else:
        slope = None

    print(f"  Brier Score: {bs:.4f} ({'✅ 达标' if bs < 0.15 else '⚠️ 偏高'})")
    if slope:
        print(f"  校准斜率: {slope:.3f} ({'✅ 良好' if 0.85 <= slope <= 1.15 else '⚠️ 偏差'})")

    # 按 offset 分组的 Brier Score（看哪个阈值区间最准）
    bs_by_offset = {}
    for p, o, off in zip(all_probs, all_outcomes, all_thresholds):
        bs_by_offset.setdefault(off, []).append((p - o) ** 2)

    print(f"  按阈值偏移的 Brier Score:")
    for off in sorted(bs_by_offset.keys()):
        bs_off = np.mean(bs_by_offset[off])
        bar = '█' * int(bs_off * 40)
        print(f"    offset {off:+3d}°F: {bs_off:.4f}  {bar}")

    # 校准曲线输出
    print(f"  校准曲线（预测概率 → 实际频率）:")
    for b in cal_bins:
        if b['n'] < 5:
            continue
        diff = b['actual_freq'] - b['mean_pred']
        direction = '↑' if diff > 0.05 else ('↓' if diff < -0.05 else '≈')
        print(f"    p={b['mean_pred']:.2f}: 实际={b['actual_freq']:.2f} {direction} (n={b['n']})")

    return {
        'city': city,
        'n_samples': len(errors),
        'n_predictions': len(all_probs),
        'bias': round(float(np.mean(errors)), 3),
        'rmse': round(float(np.sqrt(np.mean([e**2 for e in errors]))), 3),
        'brier_score': round(bs, 4),
        'calibration_slope': round(slope, 3) if slope else None,
        'calibration_bins': cal_bins
    }


def main():
    print("Weather Predict — 概率模型校准验证")
    print("基于 v5 历史数据（GFS vs WU）")
    print()

    # 加载 v5 结果（获取城市列表和基础配置）
    with open(V5_JSON) as f:
        v5_data = json.load(f)

    # 城市的 ICAO 和 utc_offset 信息
    CITY_CONFIGS = {
        "Chicago":  {"icao": "KMDW", "utc_offset": -6, "region": "US"},
        "NYC":      {"icao": "KLGA", "utc_offset": -5, "region": "US"},
        "Miami":    {"icao": "KMIA", "utc_offset": -5, "region": "US"},
        "LA":       {"icao": "KLAX", "utc_offset": -8, "region": "US"},
        "Phoenix":  {"icao": "KPHX", "utc_offset": -7, "region": "US"},
        "Austin":   {"icao": "KAUS", "utc_offset": -6, "region": "US"},
        "Boston":   {"icao": "KBOS", "utc_offset": -5, "region": "US"},
        "London":   {"icao": "EGLL", "utc_offset": 0,  "region": "EU"},
        "Madrid":   {"icao": "LEMD", "utc_offset": 1,  "region": "EU"},
        "Paris":    {"icao": "LFPG", "utc_offset": 1,  "region": "EU"},
        "Warsaw":   {"icao": "EPWA", "utc_offset": 1,  "region": "EU"},
        "Tokyo":    {"icao": "RJTT", "utc_offset": 9,  "region": "AS"},
        "Shanghai": {"icao": "ZSPD", "utc_offset": 8,  "region": "AS"},
        "Seoul":    {"icao": "RKSI", "utc_offset": 9,  "region": "AS"},
        "Beijing":  {"icao": "ZBAA", "utc_offset": 8,  "region": "AS"},
    }

    results = []
    all_bs = []

    for city in v5_data.keys():
        if city not in CITY_CONFIGS:
            print(f"\n[SKIP] {city}: 无配置")
            continue

        result = backtest_city(city, CITY_CONFIGS[city])
        if result:
            results.append(result)
            all_bs.append(result['brier_score'])

    # 汇总
    print(f"\n{'='*60}")
    print(f"汇总报告")
    print(f"{'='*60}")
    print(f"验证城市数: {len(results)}")
    if all_bs:
        print(f"平均 Brier Score: {np.mean(all_bs):.4f}")
        print(f"目标: < 0.15")
        print()
        print(f"{'城市':<12} {'Brier':>8} {'斜率':>8} {'偏差°F':>8} {'RMSE':>8} {'样本':>6}")
        print("-" * 55)
        for r in sorted(results, key=lambda x: x['brier_score']):
            slope_str = f"{r['calibration_slope']:.3f}" if r['calibration_slope'] else "N/A"
            flag = '✅' if r['brier_score'] < 0.15 else '⚠️'
            print(f"{r['city']:<12} {r['brier_score']:>7.4f}{flag} {slope_str:>8} {r['bias']:>8.2f} {r['rmse']:>8.2f} {r['n_samples']:>6}")

    # 关键诊断
    print()
    print("关键诊断：")
    overconf = [r for r in results if r['calibration_slope'] and r['calibration_slope'] < 0.85]
    underconf = [r for r in results if r['calibration_slope'] and r['calibration_slope'] > 1.15]
    if overconf:
        print(f"  ⚠️ 过度自信（斜率<0.85，概率范围过窄）: {[r['city'] for r in overconf]}")
    if underconf:
        print(f"  ⚠️ 不足自信（斜率>1.15，概率范围过宽）: {[r['city'] for r in underconf]}")

    bad_bs = [r for r in results if r['brier_score'] >= 0.15]
    if bad_bs:
        print(f"  ⚠️ Brier Score 超标: {[(r['city'], r['brier_score']) for r in bad_bs]}")

    if not overconf and not underconf and not bad_bs:
        print("  ✅ 所有城市校准指标达标")

    # 保存结果
    output = {
        'summary': {
            'n_cities': len(results),
            'mean_brier': round(float(np.mean(all_bs)), 4) if all_bs else None,
            'threshold_offsets': THRESHOLD_OFFSETS
        },
        'by_city': {r['city']: r for r in results}
    }
    out_path = "output/calibration_backtest_results.json"
    os.makedirs("output", exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n详细结果保存至: {out_path}")


if __name__ == "__main__":
    main()
