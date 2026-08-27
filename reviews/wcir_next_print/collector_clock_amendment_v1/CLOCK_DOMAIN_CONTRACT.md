# Clock Domain Contract

独立记录：wall receive ns、monotonic receive ns、exchange event timestamp、source observed/issued/first_seen、book checkpoint、official first_seen、host clock-sync status。

wall clock用于跨组件 UTC 对齐；monotonic clock只用于同进程 duration/order。两者不得直接相减。exchange timestamp 只作交易所事件顺序证据，不替代本机 receive first-seen。host sync 未知需显式保留；missing clock、wall/monotonic regression、跨 domain 比较均 fail closed。
