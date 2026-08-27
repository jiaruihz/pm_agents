# Isolation Architecture

实现文件：`scripts/analysis/forecast_quality/wcir_collector_clock_shadow.py`。它只包含 dataclass、状态机与 synthetic capacity probe；不打开 socket、不调用 REST、不读 production.yaml、不写 runtime、不连接 order/fill/settlement。

测试只在本地进程和 pytest 临时状态运行。未来若获准部署，应由独立进程读取复制的 raw feed，输出独立 epoch journal；在独立审阅通过前不得接共享 token set、现有 collector consumer 或 live process。
