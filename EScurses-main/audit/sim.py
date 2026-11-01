# 离线原型：对比三种输出策略的字节量（仅字符串构造，不代表真实终端带宽）
W,H = 80,24
cells = W*H

def strategy_percell():
    out=[]
    for y in range(1,H+1):
        for x in range(1,W+1):
            out.append(f"\033[{y};{x}H ")
    return "".join(out)

def strategy_rowbatch():
    out=["\033[?7l"]
    for y in range(1,H+1):
        out.append(f"\033[{y};1H" + " "*W)
    out.append("\033[?7h")
    return "".join(out)

def strategy_diff(changed=0.05, rowbatch=True):
    out=["\033[?7l"]
    for y in range(1,H+1):
        row=f"\033[{y};1H"
        # 只发连续变化段
        seg_start=None
        for x in range(1,W+1):
            changed_cell = ((x*7+y*13) % 100) < changed*100
            if changed_cell:
                if seg_start is None: seg_start=x
            else:
                if seg_start is not None:
                    seg_end=x-1
                    row+=f"\033[{seg_start}G"+"="*(seg_end-seg_start+1)
                    seg_start=None
        if seg_start is not None:
            row+=f"\033[{seg_start}G"+"="*(W-seg_start+1)
        out.append(row)
    out.append("\033[?7h")
    return "".join(out)

for name, s in [("per-cell (current)", strategy_percell()),
                ("row-batch full", strategy_rowbatch()),
                ("diff 5% row-batch", strategy_diff(0.05)),
                ("diff 20% row-batch", strategy_diff(0.20))]:
    print(f"{name:22s} bytes/frame={len(s):7d}  ratio={len(s)/len(strategy_percell()):.3f}  @60fps={len(s)*60/1024:.1f} KB/s")
