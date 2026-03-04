# weather_theta_no_v1

## 策略说明

- 类型：天气方向策略（NO 侧）
- key：`weather_theta_no_v1`
- 核心：赚取不确定性随时间下降带来的 carry

## 目录内容

- `package.py`：策略包元信息
- `params.example.json`：策略参数示例
- `run_paper.sh`：paper 启动脚本

## 快速执行

```bash
bash src/domains/pmm/strategy_packs/weather_theta_no_v1/run_paper.sh
```

更多策略细节：
- `src/domains/pmm/docs/WEATHER_THETA_NO_PROGRESS.md`
