"""端到端集成测试：示例脚本能否在无人值守环境下真实跑通。

这里用假终端 + 假键盘驱动 ``examples/`` 里的脚本，验证的是：
初始化 → 循环 → 绘制 → 按键 → 退出 → 终端恢复 的完整链路。
"""

from __future__ import annotations

import importlib
import unittest
from unittest import mock

import escurses

from .helpers import FakeBackend, FakeStream


class TestExamplesHeadless(unittest.TestCase):
    def setUp(self) -> None:
        escurses._session = None
        escurses._legacy_clock = None
        escurses.reset_config()
        self._real_is_interactive = escurses.is_interactive
        escurses.is_interactive = lambda: True  # 让示例认为自己在真实终端里

    def tearDown(self) -> None:
        escurses.is_interactive = self._real_is_interactive
        if escurses._session is not None:
            escurses._session.close()
        escurses._session = None
        escurses.reset_config()

    def test_hello_game_runs(self) -> None:
        demo = importlib.import_module("examples.hello_game")
        stream = FakeStream(tty=True)
        backend = FakeBackend(["q"])
        real_game = demo.Game

        def factory(**kwargs):
            return real_game(
                width=40,
                height=12,
                stream=stream,
                force_ansi=True,
                backend=backend,
                **kwargs
            )

        demo.Game = factory
        try:
            with mock.patch.object(escurses.Game, "wait_for_key", return_value=None):
                code = demo.main()
        finally:
            demo.Game = real_game

        self.assertEqual(code, 0)
        output = stream.getvalue()
        self.assertIn("\x1b[?1049h", output)   # 进入了备用屏幕
        self.assertIn("\x1b[?1049l", output)   # 也退出来了
        self.assertIn("EScurses", output)

    def test_legacy_demo_runs(self) -> None:
        demo = importlib.import_module("examples.legacy_demo")
        stream = FakeStream(tty=True)
        # 先喂一个移动键让循环画一帧，再喂退出键
        backend = FakeBackend(["aq"])
        real_init = escurses.initGame

        def init(**kwargs):
            return real_init(
                width=40,
                height=12,
                stream=stream,
                force_ansi=True,
                backend=backend,
                **kwargs
            )

        escurses.initGame = init
        try:
            code = demo.main()
        finally:
            escurses.initGame = real_init

        self.assertEqual(code, 0)
        output = stream.getvalue()
        self.assertIn("旧 API 演示", output)
        self.assertIn("\x1b[?1049l", output)


if __name__ == "__main__":
    unittest.main()
