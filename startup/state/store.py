"""响应式 Store 实现，参考原始 state/store.ts 的 34 行实现。"""

from __future__ import annotations

from typing import Callable, Generic, Set, TypeVar

T = TypeVar("T")
Listener = Callable[[], None]
OnChange = Callable[[dict], None]


class Store(Generic[T]):
    """响应式状态容器，支持发布-订阅和不可变更新。"""

    def __init__(self, initial_state: T, on_change: OnChange[T] | None = None) -> None:
        self._state: T = initial_state
        self._listeners: Set[Listener] = set()
        self._on_change: OnChange[T] | None = on_change

    def get_state(self) -> T:
        return self._state

    def set_state(self, updater: Callable[[T], T]) -> None:
        prev = self._state
        next_state = updater(prev)
        if next_state is prev:
            return
        self._state = next_state
        if self._on_change:
            self._on_change({"new_state": next_state, "old_state": prev})
        for listener in self._listeners:
            listener()

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)
        def unsubscribe() -> None:
            self._listeners.discard(listener)
        return unsubscribe


def create_store(initial_state: T, on_change: OnChange[T] | None = None) -> Store[T]:
    """创建一个响应式 Store 实例。"""
    return Store(initial_state, on_change)
