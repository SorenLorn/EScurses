import os, sys, io, time
sys.path.insert(0, r"E:/workbuddy-Demo/EScurses-main")
import escurses as es

# fake terminal 80x24
os.get_terminal_size = lambda *a, **k: os.terminal_size((80, 24))
es.initGame()
print("screenWidth/Height:", es._screenWidth, es._screenHeight)
print("row len:", len(es._screen[0]), "rows:", len(es._screen))

# 1) out-of-range x
for args in [(80,1,'A'), (0,0,'B'), (-5,-5,'C'), (81,1,'D')]:
    try:
        es.drawChar(*args)
        print("drawChar", args, "-> ok (silent), cell =", es._screen[args[1]-1][args[0]-1])
    except Exception as e:
        print("drawChar", args, "->", type(e).__name__, e)

# 2) wide char
es.initGame()
es.drawChar(1,1,'中'); es.drawChar(2,1,'X')
print("wide: cell(1,1)=", es._screen[0][0], " cell(2,1)=", es._screen[0][1])

# 3) empty string / multi-char
es.initGame()
es.drawChar(1,1,'')
print("empty cell:", repr(es._screen[0][0][0]))
es.drawChar(1,1,'AB')
print("multichar cell:", repr(es._screen[0][0][0]))

# 4) negative index silent write demo on fresh buffer
es.initGame()
es.drawChar(-1,-1,'Z')
print("after drawChar(-1,-1): last cell =", es._screen[-1][-1])

# 5) clear before init
import importlib
m = importlib.reload(es)
try:
    m.clear()
except Exception as e:
    print("clear() before initGame:", type(e).__name__, e)

# 6) sleepForFrame before frameStart / fps=0
importlib.reload(es)
try:
    m.sleepForFrame(60)
except Exception as e:
    print("sleepForFrame before frameStart:", type(e).__name__, e)
m.frameStart()
try:
    m.sleepForFrame(0)
except Exception as e:
    print("sleepForFrame(0):", type(e).__name__, e)
