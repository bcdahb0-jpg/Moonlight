"""屏幕理解与陪聊能力（screen_awareness）独立包。

职责边界（见 docs/screen-awareness-companion-plan.md）：
- 只做「看见」：接收前端采集的活动窗口帧，去重、视觉摘要、短时上下文缓存；
- 不做「操作」：无写文件 / 点击 / 键盘权限，与桌面控制权限域完全分离；
- 隐私铁律：原始图像仅存内存（分析完立即释放），不落盘、不写日志标题全文、
  不进入四层记忆；关闭开关后无残留帧。

模块：
- models.py   结构化协议：ScreenFrame / ScreenSnapshot / ScreenStatus / PolicyDecision
- metrics.py  捕获、去重、上传、视觉分析、主动决策的耗时/次数/token 指标
- privacy.py  后端二次校验：应用黑名单 / 标题关键词 / Moonlight 自身窗口
- analyzer.py 视觉模型调用与严格结构化输出（fail-soft）
- service.py  会话级短时上下文缓存（TTL + 窗口身份失效 + 单飞并发控制）
- policy.py   是否分析、是否主动陪聊（冷却、去重、场景判断）
- route.py    localhost-only API：状态 / 分析 / 清除 / 指标
"""

from .models import (
    ScreenWindowInfo,
    ScreenFrame,
    ScreenSnapshot,
    ScreenStatus,
    PolicyDecision,
    ScreenConfig,
)

__all__ = [
    "ScreenWindowInfo",
    "ScreenFrame",
    "ScreenSnapshot",
    "ScreenStatus",
    "PolicyDecision",
    "ScreenConfig",
]
