"""playmate 包（P4 游戏陪玩）。"""
from .game import GAMES, get_game, match_game  # noqa: F401
from .events import classify_event, get_event_broker  # noqa: F401
from .route import init_playmate_route  # noqa: F401
