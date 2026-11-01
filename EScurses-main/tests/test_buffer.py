"""缓冲区与双缓冲测试。"""

from __future__ import annotations

import unittest

from escurses.buffer import BLANK, CONTINUATION, Buffer, Cell, DoubleBuffer
from escurses.exceptions import InvalidConfigError


class TestBufferBasics(unittest.TestCase):
    def test_initial_state_is_blank(self) -> None:
        buf = Buffer(4, 2)
        self.assertEqual((buf.width, buf.height), (4, 2))
        for y in range(2):
            for x in range(4):
                self.assertIs(buf.get(x, y), BLANK)

    def test_invalid_size(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Buffer(0, 5)
        with self.assertRaises(InvalidConfigError):
            Buffer(5, -1)

    def test_set_and_get(self) -> None:
        buf = Buffer(3, 3)
        cell = Cell("A")
        buf.set(1, 2, cell)
        self.assertEqual(buf.get(1, 2), cell)
        self.assertIs(buf.get(0, 2), BLANK)

    def test_reset(self) -> None:
        buf = Buffer(3, 3)
        buf.set(0, 0, Cell("X"))
        buf.reset()
        self.assertIs(buf.get(0, 0), BLANK)


class TestBufferResize(unittest.TestCase):
    def test_grow(self) -> None:
        buf = Buffer(2, 2)
        buf.set(0, 0, Cell("A"))
        buf.resize(4, 3)
        self.assertEqual(buf.get(0, 0).char, "A")
        self.assertIs(buf.get(3, 2), BLANK)

    def test_shrink_keeps_overlap(self) -> None:
        buf = Buffer(4, 4)
        buf.set(0, 0, Cell("A"))
        buf.set(3, 3, Cell("Z"))
        buf.resize(2, 2)
        self.assertEqual(buf.get(0, 0).char, "A")
        self.assertEqual((buf.width, buf.height), (2, 2))

    def test_same_size_is_noop(self) -> None:
        buf = Buffer(2, 2)
        buf.set(0, 0, Cell("A"))
        buf.resize(2, 2)
        self.assertEqual(buf.get(0, 0).char, "A")


class TestBufferCopy(unittest.TestCase):
    def test_copy_from(self) -> None:
        src = Buffer(3, 2)
        src.set(1, 1, Cell("Q"))
        dst = Buffer(3, 2)
        dst.copy_from(src)
        self.assertTrue(dst.same_as(src))

    def test_copy_from_size_mismatch(self) -> None:
        with self.assertRaises(InvalidConfigError):
            Buffer(2, 2).copy_from(Buffer(3, 2))

    def test_cells_are_shared_not_deep_copied(self) -> None:
        src = Buffer(2, 1)
        cell = Cell("A")
        src.set(0, 0, cell)
        dst = Buffer(2, 1)
        dst.copy_from(src)
        # 单元格不可变，复制行即可，不需要深拷贝对象
        self.assertIs(dst.get(0, 0), cell)


class TestBufferText(unittest.TestCase):
    def test_row_text_skips_continuation(self) -> None:
        buf = Buffer(6, 1)
        buf.set(0, 0, Cell("中", 2, False))
        buf.set(1, 0, CONTINUATION)
        buf.set(2, 0, Cell("A"))
        self.assertEqual(buf.row_text(0), "中A")

    def test_row_text_rstrip(self) -> None:
        buf = Buffer(4, 1)
        buf.set(0, 0, Cell("A"))
        self.assertEqual(buf.row_text(0), "A")
        self.assertEqual(buf.row_text(0, rstrip=False), "A   ")

    def test_row_is_blank(self) -> None:
        buf = Buffer(3, 2)
        self.assertTrue(buf.row_is_blank(0))
        buf.set(2, 0, Cell("A"))
        self.assertFalse(buf.row_is_blank(0))
        self.assertTrue(buf.row_is_blank(1))

    def test_lines(self) -> None:
        buf = Buffer(3, 2)
        buf.set(0, 0, Cell("A"))
        self.assertEqual(buf.lines(), ["A", ""])


class TestDoubleBuffer(unittest.TestCase):
    def test_initially_invalid(self) -> None:
        db = DoubleBuffer(4, 3)
        self.assertFalse(db.valid)
        self.assertIsNone(db.pending_changes())

    def test_commit_syncs_front(self) -> None:
        db = DoubleBuffer(4, 3)
        db.back.set(0, 0, Cell("A"))
        db.commit()
        self.assertTrue(db.valid)
        self.assertTrue(db.front.same_as(db.back))
        self.assertEqual(db.pending_changes(), 0)

    def test_commit_does_not_clear_back(self) -> None:
        """back 是持久场景缓冲，提交后内容必须保留（旧库语义）。"""
        db = DoubleBuffer(4, 3)
        db.back.set(0, 0, Cell("A"))
        db.commit()
        self.assertEqual(db.back.get(0, 0).char, "A")

    def test_pending_changes_counts_cells(self) -> None:
        db = DoubleBuffer(4, 3)
        db.commit()
        db.back.set(0, 0, Cell("A"))
        db.back.set(1, 0, Cell("B"))
        self.assertEqual(db.pending_changes(), 2)

    def test_invalidate(self) -> None:
        db = DoubleBuffer(4, 3)
        db.commit()
        db.invalidate()
        self.assertFalse(db.valid)
        self.assertIsNone(db.pending_changes())

    def test_clear(self) -> None:
        db = DoubleBuffer(4, 3)
        db.back.set(0, 0, Cell("A"))
        db.commit()
        db.clear()
        self.assertIs(db.back.get(0, 0), BLANK)
        self.assertFalse(db.valid)

    def test_resize_invalidates(self) -> None:
        db = DoubleBuffer(4, 3)
        db.commit()
        db.resize(6, 4)
        self.assertFalse(db.valid)
        self.assertEqual((db.width, db.height), (6, 4))

    def test_resize_same_size_keeps_valid(self) -> None:
        db = DoubleBuffer(4, 3)
        db.commit()
        db.resize(4, 3)
        self.assertTrue(db.valid)


if __name__ == "__main__":
    unittest.main()
