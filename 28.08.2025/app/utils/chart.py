import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def save_signal_chart(ohlcv, entry, sl, tp1, tp2, tp3, symbol:str, out_dir:str="data/charts") -> str:
    os.makedirs(out_dir, exist_ok=True)
    closes = [c[4] for c in ohlcv[-120:]] if ohlcv else []
    fig, ax = plt.subplots(figsize=(10,4))
    ax.plot(closes)
    ax.axhline(entry, linestyle='--', linewidth=1)
    ax.axhline(sl, linestyle='--', linewidth=1)
    ax.axhline(tp1, linestyle='--', linewidth=1)
    ax.axhline(tp2, linestyle='--', linewidth=1)
    ax.axhline(tp3, linestyle='--', linewidth=1)
    ax.set_title(f"{symbol} – plan zagrania (Entry/SL/TP)")
    path = os.path.join(out_dir, f"{symbol.replace('/','_')}.png")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
