import os
import threading

from .emotion_analyzer import EmotionAnalyzer, EMOTIONS
from .emotion_tracker import EmotionTracker

__all__ = ["EmotionAnalyzer", "EmotionTracker", "EMOTIONS", "get_emotion_tracker"]

_tracker: EmotionTracker = None
_tracker_lock = threading.Lock()


def get_emotion_tracker() -> EmotionTracker:
    """全局情绪跟踪器单例（线程安全）。"""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _state_path = None
                try:
                    _root = os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                    )
                    _state_path = os.path.join(_root, "emotion_state.json")
                except Exception:
                    _state_path = None
                _tracker = EmotionTracker(state_path=_state_path)
    return _tracker

