# Helsinki path morphology optimization v2

## 结果

- `fade`：selected=`dynamic_hgb`；base Brier/logloss=0.02771/0.09673，dynamic=0.02563/0.08901。
- `fresh_runway`：selected=`dynamic_hgb`；base Brier/logloss=0.13706/0.41834，dynamic=0.09932/0.31553。
- candidate artifact SHA `62c519f9f9cceeb8012731d27d7c6f32aeb715dbf9151f5c09878ac2a354353f`；train_end=2025-12-31；尚未执行market dev replay，因此不是最终freeze。

## 边界

- 只在2025 expanding OOF选择；7/15–29只允许下一步diagnostic replay，7/31+保持clean forward。
- 不加价格、时钟或path hard gate；specialist输出连续30/60/120/EOD概率。
- 不改live，actual fill=0。
