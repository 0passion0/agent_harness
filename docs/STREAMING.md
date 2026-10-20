# 流式与恢复

engine/streaming.py 投影原生模型、工具和运行事件；desktop.py 承担 RPC、取消与审批恢复。终态通知前释放执行资源，工具参数和输出只由桌面 callback 按需采集。只恢复当前格式的检查点，不适配旧执行图。
