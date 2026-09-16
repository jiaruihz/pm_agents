# CITY.md

# Polymarket Weather Markets - City Config

## 0. 使用说明
这份文件只维护“城市配置”。

不写分析，不写盘口观点，不写交易建议。
每次做单时：
1. 先来这里找站点和链接
2. 再按 `DATA_SOURCE.md` 的 SOP 去看源

---

## 1. 全局约定

### 1.1 字段说明
- `city_key`: 内部城市键
- `display_name`: 市场标题里的城市名
- `settlement_station`: Polymarket rules 实际结算站点
- `station_code`: 机场/站点代码
- `default_unit`: 默认常见单位，仅作提示
- `must_check_rules_each_time`: 每次是否必须重新核 rules
- `timezone`: 站点本地时区
- `wu_history_url`: 结算真值页
- `wu_hourly_url`: 主交易锚页
- `same_stack_reference`: 同栈参考页
- `official_secondary_source`: 官方/半官方校验页
- `event_url_example`: 当前或近期 event 示例链接
- `weather_traits`: 简短天气特征
- `common_mispricing`: 常见错价来源
- `notes`: 其他备注

### 1.2 v1 简化规则
v1 不强制维护：
- 经纬度
- 所有历史 event 链接
- 所有辅助源

先保证：
- 站点对
- 单位对
- URL 对
- 时区对

---

## 2. 城市配置

## 2.1 Seoul
```yaml
city_key: seoul
display_name: Seoul
settlement_station: Incheon Intl Airport Station
station_code: RKSI
default_unit: C
must_check_rules_each_time: true
timezone: Asia/Seoul

wu_history_url: https://www.wunderground.com/history/daily/kr/incheon/RKSI
wu_hourly_url: https://www.wunderground.com/hourly/kr/incheon/RKSI

same_stack_reference:
  - https://www.weather.com/

official_secondary_source:
  - https://amo.kma.go.kr/eng/airport.do?icaoCode=RKSI

event_url_example:
  - https://polymarket.com/event/highest-temperature-in-seoul-on-march-12-2026

weather_traits:
  - 市场标题写 Seoul，但实际结算站点是仁川机场，不是首尔市区
  - 机场口径通常比市区更冷
  - 海风/机场暴露条件会放大与城市页的差异

common_mispricing:
  - 把首尔市区页当成结算锚
  - 把 Incheon 城市页当成 RKSI 机场站
  - 忽略 WU 同站点 hourly，只看城市 forecast

notes:
  - 首先核对 unit 是否仍是摄氏度
  - 先看 RKSI，不要先看 Seoul city page