import sys
import os, subprocess
import time
if os.name == 'posix':
    import termios, tty, select
if os.name == 'nt':
    import msvcrt

_screen = []
_screenWidth = None
_screenHeight = None

_frameStartTime = None
def frameStart():
    global _frameStartTime
    _frameStartTime = time.perf_counter()

def linuxTerminalCheck():
    isTTY = os.isatty(sys.stdin.fileno())
    return isTTY

def getTerminalSize():
    scrSize = os.get_terminal_size()
    maxX = scrSize.columns
    maxY = scrSize.lines
    return (maxX, maxY)

def initGame():
    scrSize = getTerminalSize()
    global _screenWidth
    global _screenHeight
    _screenWidth = scrSize[0]
    _screenHeight = scrSize[1]
    global _screen
    _screen = []
    for i in range(_screenHeight):
        _screen.append([(' ', i+1, j+1) for j in range(_screenWidth - 1)])
def clear():
    sys.stdout.write('\033[2J')
    global _screen
    _screen = []
    for i in range(_screenHeight):
        _screen.append([(' ', i+1, j+1) for j in range(_screenWidth - 1)])

def drawChar(x, y, char):
    _screen[y-1][x-1] = (char, y, x)

def getch(nodelay=True):
    if os.name == 'nt':
        if nodelay:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                return ch
            return None
        elif not nodelay:
            ch = msvcrt.getwch()
            return ch
    elif os.name == 'posix':
        if nodelay:
            fd = sys.stdin.fileno()
            oldSettings = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                if select.select([fd], [], [], 0):
                    ch = sys.stdin.read(1)
                    return ch
                return None
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, oldSettings)
        elif not nodelay:
            fd = sys.stdin.fileno()
            oldSettings = termios.tcgetattr(fd)
            try: 
                ch = sys.stdin.read(1)
                return ch
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, oldSettings)

def refresh():
    for line in _screen:
        for char in line:
            sys.stdout.write(f'\033[{char[1]};{char[2]}H{char[0]}')
    sys.stdout.flush()

def cursorEcho(echo=False):
    if echo:
        sys.stdout.write('\033[?25h')
    if not echo:
        sys.stdout.write('\033[?25l')

def exitGame():
    sys.stdout.write('\033[?25h')
    if os.name == 'nt': subprocess.run("cls", shell=True)
    if os.name == 'posix': subprocess.run("clear", shell=True)
    sys.stdout.flush()
    sys.exit()

def sleepForFrame(fps):
    frameTime = 1 / fps
    elapsedTime = time.perf_counter() - _frameStartTime
    time.sleep(max(0, (frameTime - elapsedTime) - 0.01))
    while time.perf_counter() - _frameStartTime < 0.01:
        pass