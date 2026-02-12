# PMM (Personal Market Maker) 测试套件说明文档

## 概述
本测试套件旨在验证 PMM (Personal Market Maker) 及其相关 Polymarket 组件的功能正确性、稳定性和性能。测试覆盖了从底层配置到高层交易流程的所有关键组件。

## 目录结构
```
tests/
└── pmm_tests/
    ├── __init__.py
    ├── test_config.py          # 配置模块测试
    ├── test_signals.py         # 信号计算模块测试
    ├── test_strategies.py      # 策略模块测试
    ├── test_order_manager.py   # 订单管理模块测试
    ├── test_data.py           # 数据处理模块测试
    ├── test_polymarket_components.py  # Polymarket组件测试
    └── test_integration.py    # 集成测试
```

## 测试模块详情

### 1. test_config.py
- **功能**: 验证 PMM 配置系统的正确性
- **测试内容**:
  - 默认配置创建
  - 环境变量配置加载
  - 引用级别的有效性检查
  - 多级报价配置

### 2. test_signals.py
- **功能**: 验证市场微结构信号函数
- **测试内容**:
  - 加权中间价计算
  - 公平中间价计算
  - 库存信号计算
  - 已实现波动率计算
  - 动量指标计算
  - 订单流不平衡计算
  - 必需价差计算
  - 中间价附近的深度计算

### 3. test_strategies.py
- **功能**: 验证交易策略模块
- **测试内容**:
  - 策略输入数据结构
  - 报价目标数据结构
  - 策略注册表功能
  - 模拟策略执行

### 4. test_order_manager.py
- **功能**: 验证订单管理器功能
- **测试内容**:
  - 死区带检查逻辑
  - 订单替换判断
  - 订单ID提取
  - 多级订单差异计算
  - 订单取消和创建逻辑

### 5. test_data.py
- **功能**: 验证数据处理模块
- **测试内容**:
  - 订单簿工具函数
  - 最佳买卖报价提取
  - 中间价计算
  - 价差计算
  - HTTP 客户端初始化

### 6. test_polymarket_components.py
- **功能**: 验证 Polymarket 相关组件
- **测试内容**:
  - Polymarket 客户端初始化
  - Gamma 客户端初始化
  - Executor 初始化
  - Trader 初始化
  - retain_keys 工具函数

### 7. test_integration.py
- **功能**: 验证组件间的集成
- **测试内容**:
  - PMM 配置与引擎集成
  - Executor 与其依赖项集成
  - Trader 与其依赖项集成
  - 端到端工作流结构

## 运行测试

### 运行所有测试
```bash
cd /path/to/pm_agents
source venv/bin/activate
python -m pytest tests/pmm_tests/ -v
```

### 运行特定模块测试
```bash
python -m pytest tests/pmm_tests/test_config.py -v
```

### 运行单个测试
```bash
python -m pytest tests/pmm_tests/test_config.py::TestPMMConfig::test_default_config_creation -v
```

## 测试覆盖率
- **总测试数**: 51
- **通过率**: 100%
- **覆盖范围**: PMM 核心模块及 Polymarket 组件

## 维护指南
1. 当添加新功能时，应相应地添加测试用例
2. 修改现有功能时，确保更新相关测试
3. 保持测试的独立性和可重复性
4. 使用适当的模拟对象避免外部依赖
5. 确保测试用例具有描述性的名称和清晰的断言

## 注意事项
- 某些测试可能会访问外部 API，在离线环境下可能需要跳过
- 测试数据应使用模拟值而非真实数据
- 遵循 AAA 模式 (Arrange, Act, Assert) 编写测试