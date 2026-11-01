import os, sys, time
sys.path.insert(0, r"E:/workbuddy-Demo/EScurses-main")
import escurses as es

for target in (30, 60, 120, 240):
    n = 40
    t0 = time.perf_counter()
    for _ in range(n):
        es.frameStart()
        es.sleepForFrame(target)
    dt = time.perf_counter() - t0
    print(f"target {target:3d} FPS -> actual {n/dt:6.2f} FPS  (avg {dt/n*1000:.2f} ms/frame)")
