import os, sys, io, time
sys.path.insert(0, r"E:/workbuddy-Demo/EScurses-main")
import escurses as es

for (w,h) in [(80,24),(120,40),(200,60)]:
    os.get_terminal_size = lambda *a, _w=w, _h=h, **k: os.terminal_size((_w,_h))
    es.initGame()
    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
    t0 = time.perf_counter()
    N = 20
    for _ in range(N):
        buf.seek(0); buf.truncate(0)
        es.refresh()
    dt = (time.perf_counter()-t0)/N
    sys.stdout = old
    payload = len(buf.getvalue())
    cells = len(es._screen)*len(es._screen[0])
    print(f"{w}x{h}: cells={cells} bytes/frame={payload} bytes/cell={payload/cells:.1f} time={dt*1000:.2f}ms")
