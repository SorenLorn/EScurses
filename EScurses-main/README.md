# EScurses

A lightweight, **zero-dependency** terminal game library for Python.
Draw characters, read the keyboard, and keep a stable frame rate — on Windows and Linux.

[中文文档](./README-zh.md) · [Architecture](./REFACTOR_REPORT.md) · [Audit](./CODE_AUDIT.md)

```python
from escurses import Game, Key

with Game(fps=30) as game:
    game.draw_text(1, 1, "Press arrow keys, Q to quit")

    x, y = 10, 5
    while game.is_running:
        game.frame_start()

        for event in game.poll_keys():
            if event.key is Key.CHAR and event.char == "q":
                game.quit()
            elif event.key is Key.UP:
                y -= 1
            elif event.key is Key.DOWN:
                y += 1

        game.draw_char(x, y, "@")
        game.present()
        game.sleep_for_frame()
```

---

## Install

```bash
pip install escurses            # from source / PyPI when published
```

Or just copy the `escurses/` directory next to your script — no dependencies, no build step.

- Python **3.9+**
- Windows 10/11 console or Windows Terminal
- Linux / any ANSI terminal
- macOS: **not tested** by the maintainer. It should work (POSIX backend), but it is not verified.

## Why EScurses

| | |
|---|---|
| **Zero dependencies** | Standard library only. Nothing to compile, nothing to pin. |
| **Double buffering + diff rendering** | Only changed cells are written. An idle frame emits **0 bytes**. |
| **Real frame limiter** | Deadline-based, drift-free, no busy waiting. `fps=60` means 60. |
| **Safe terminal lifecycle** | Context manager + `try/finally`; the terminal is restored even on exceptions. |
| **CJK-aware** | Display width comes from `unicodedata`, not `len()`. Chinese/Japanese text does not shift. |

## What's new in 0.2.0

This release is a full rewrite of the internals (Iteration A of the roadmap).
The public API is unchanged; the behavior is far more predictable.

| Before | After |
|---|---|
| Every cell redrawn with its own cursor-position escape (≈16 KB/frame at 80×24) | Only changed spans; 80×24 full frame ≈ 2 KB, idle frame 0 bytes |
| `sleepForFrame(60)` actually ran at ~100 FPS and burned a CPU core | Hits the target within ~1%, 0% CPU while waiting |
| Buffer was 1 column narrower than the reported size; negative coordinates wrote silently to the wrong cell | Buffer matches the reported size; out-of-range draws are clipped (or raise in strict mode) |
| Arrow keys were unusable (prefix bytes leaked as characters) | Arrow keys, Home/End/PgUp/PgDn, F1–F12 are decoded |
| A CJK character occupied one cell and was overwritten by its neighbour | Wide characters occupy two cells; layout stays aligned |
| `exitGame()` called `sys.exit()` and wiped the screen | Restores the terminal; screen wiping is opt-in |

## Modern API

### Lifecycle

```python
with Game(fps=60, alt_screen=True) as game:
    ...
```

`Game` is a context manager. Whatever happens inside — normal exit, `raise`, `Ctrl+C` —
the terminal is restored: cursor visible, alternate screen left, wrap mode reset, SGR reset.

```python
game = Game(alt_screen=False)   # keep the game on the normal screen
game.start()
try:
    ...
finally:
    game.close()                # idempotent
```

| Option | Default | Meaning |
|---|---|---|
| `fps` | `60` | Target frame rate (0.1–1000) |
| `strict` | `False` | Raise `OutOfBoundsError` instead of clipping |
| `alt_screen` | `True` | Use the alternate screen buffer |
| `hide_cursor` | `True` | Hide the cursor while playing |
| `width` / `height` | terminal size | Fixed logical size (useful for tests) |

### Drawing

Coordinates are **1-based**: `(1, 1)` is the top-left cell.

```python
game.draw_char(5, 3, "P")        # one character
game.draw_text(1, 1, "你好，世界")  # a string, advanced by display width
game.fill(1, 10, 40, 12, "#")    # rectangle
game.clear()                     # wipe the scene (next present() repaints)
```

Out-of-range coordinates are **clipped by default**, never written to a wrong cell:

```python
game.draw_char(0, 0, "X")        # ignored, returns False
game.draw_char(999, 999, "X")    # ignored, returns False

game.strict = True
game.draw_char(0, 0, "X")        # raises OutOfBoundsError (an IndexError subclass)
```

### Rendering

```python
game.present()          # write changed cells only
game.present(force=True)  # full repaint
game.draw_text(1, 1, "hi")
game.refresh()          # alias of present(), for legacy code
```

`present()` returns `RenderStats(bytes_written, cells, spans, cursor_moves, elapsed)`.

The back buffer is **persistent**: drawing the same scene every frame costs almost nothing,
and drawing once keeps it on screen. You do not need to clear every frame.

### Input

```python
for event in game.poll_keys():        # non-blocking, drains the queue
    print(event.key, event.char)

event = game.read_key(timeout=1.0)    # blocking with timeout
key = game.getch()                    # legacy style: returns str | None
```

`Key` covers `CHAR, ENTER, TAB, BACKSPACE, ESC, SPACE, CTRL_C, UP, DOWN, LEFT, RIGHT,
HOME, END, PAGE_UP, PAGE_DOWN, INSERT, DELETE, F1..F12, UNKNOWN`.

### Frame loop

```python
game.frame_start()
# ... update + draw ...
game.present()
game.sleep_for_frame()
print(game.frame_stats.fps, game.dt)
```

Or let `Game` drive it:

```python
game.run(step_function, fps=60)   # calls step(game) each frame
```

### Unicode width

```python
from escurses import text_width

text_width("abc")      # 3
text_width("中文")      # 4  (not len() == 2)
text_width("e\u0301")  # 1  (combining mark stays with its base)
```

## Legacy API

The original 11 functions still work, with the same names, argument order and 1-based
coordinates. Old code does not need to change:

```python
import escurses

escurses.initGame()
escurses.cursorEcho(False)
try:
    while True:
        escurses.frameStart()
        escurses.clear()
        escurses.drawChar(5, 3, "P")
        escurses.refresh()
        escurses.sleepForFrame(30)
        if escurses.getch() == "q":
            break
finally:
    escurses.exitGame(exit_process=False)
```

### Migration notes (6 behavioral changes)

| # | Change | Why | How to keep old behavior |
|---|---|---|---|
| 1 | Writable width is now the full terminal width (was `width - 1`) | The old off-by-one made a valid coordinate crash | Nothing to do; the reserved column was an internal hack |
| 2 | Out-of-range draws are clipped instead of raising `IndexError` / silently corrupting | Negative indices used to write to random cells | `escurses.configure(strict=True)` restores exceptions |
| 3 | `drawChar(1, 1, "AB")` writes two cells sequentially | It used to squeeze two chars into one cell and shift the row | None needed — the old result was broken output |
| 4 | `exitGame()` no longer clears the screen | Wiping the screen destroyed the final frame | `exitGame()` still raises `SystemExit(0)` by default; pass `exit_process=False` in libraries/tests |
| 5 | `initGame()` hides the cursor and enters the alternate screen | `initGame` used to do almost nothing | `initGame(alt_screen=False, hide_cursor=False)` |
| 6 | `getch()` returns the raw sequence for arrow keys (`"\x1b[A"` / `"\xe0H"`) | There was no arrow support at all before | Use `game.poll_keys()` and `Key` |

Only direct access to internal variables (`escurses._screen`, …) is not preserved.
The README always advised against it.

## Known limitations

- **Color is not implemented yet.** `detect_color_support()` reports the terminal's
  capability (`NONE / BASIC / XTERM256 / TRUECOLOR`) and the renderer never emits SGR
  sequences, but there is no `fg=`/`bg=` parameter yet. That is Iteration B.
- **`KEY_UP` is not provided.** A character stream cannot reliably report key release on
  either platform, so `supports_key_up` is `False` rather than faking it.
- **Ambiguous-width characters** (e.g. `±`, `→`, box-drawing) are treated as width 1.
  Terminals configured with `ambiguous-width=2` will disagree.
- **Complex emoji** (ZWJ families, flags with modifiers) are estimated at 2 columns.
  Some terminals render them wider; the library cannot detect that.
- **Linux and macOS were not executed** during this refactor. The POSIX backend is
  covered by parser tests and mirrors the documented termios behavior, but it has not
  been run on a real Linux tty by the maintainer.
- **Mouse reporting** is parsed as `Key.UNKNOWN`; no mouse events yet (Iteration B).

## Architecture

```
escurses/
├── config.py         strict/alt_screen/fps … single validation entry
├── exceptions.py     one exception hierarchy
├── unicode_utils.py  display width + grapheme splitting (lru_cache)
├── buffer.py         Cell / Buffer / DoubleBuffer
├── screen.py         1-based coordinates, clipping, wide-char invariants
├── renderer.py       diff rendering, ANSI output, stats
├── terminal.py       save/restore, alt screen, capability detection
├── input.py          unified input + ESC/CSI/SS3 parser
├── _win_input.py     msvcrt backend   (imported only on Windows)
├── _posix_input.py   termios backend  (imported only on POSIX)
├── timing.py         Clock: deadline scheduling, no busy wait
└── game.py           lifecycle, main loop, legacy API
```

Dependency direction is strictly one-way: `game → {terminal, input, renderer, screen, timing}`,
`renderer → screen`, `screen → buffer → unicode_utils`. `buffer` and `screen` never import
`terminal`; `renderer` never imports `input`.

See [REFACTOR_REPORT.md](./REFACTOR_REPORT.md) for the measured before/after numbers,
and [CODE_AUDIT.md](./CODE_AUDIT.md) for the defect list this work was based on.

## Development

```bash
python -m unittest discover -s tests -t .   # 264 tests, ~1.6 s
python benchmarks/bench_render.py           # old vs new output volume
python benchmarks/bench_fps.py              # old vs new frame pacing
python examples/hello_game.py               # interactive demo
```

`-t .` is required: `tests/` is a package that uses relative imports, so without an
explicit top-level directory `unittest` imports the modules as top-level and every
`from .helpers import ...` fails.

## License

No license file is included in this repository yet. Until the maintainer adds one,
the code is "all rights reserved" by default — please open an issue before redistributing.
