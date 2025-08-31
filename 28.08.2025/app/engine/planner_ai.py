import json, random
from typing import Dict
from ..config import SETTINGS
try:
    from openai import OpenAI
except Exception:
    OpenAI = None

def _clamp(v, lo, hi):
    return max(lo, min(hi, v))

def _heuristic(side: str, last: float, vola: float) -> Dict:
    step = max(0.003, min(0.02, (vola / max(last,1e-9)) if last else 0.01))
    if side == 'LONG':
        entry = last * (1 - 0.2*step)
        sl    = last * (1 - 2.5*step)
        tp1   = last * (1 + 1.2*step)
        tp2   = last * (1 + 2.5*step)
        tp3   = last * (1 + 4.0*step)
    else:
        entry = last * (1 + 0.2*step)
        sl    = last * (1 + 2.5*step)
        tp1   = last * (1 - 1.2*step)
        tp2   = last * (1 - 2.5*step)
        tp3   = last * (1 - 4.0*step)
    risk = abs(entry - sl)
    reward = abs(tp1 - entry)
    rr = (reward / (risk+1e-9)) if risk>0 else 0.0
    conf = 0.70 + 0.2*random.random()
    succ = 0.62 + 0.2*random.random()
    return dict(action=side, entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, rr=rr, confidence=conf, success=succ,
                reason=f"Heurystyczny plan {side} przy zmienności ~{step:.4f}")

def plan_openai(context: Dict, side_hint: str, last: float, vola: float) -> Dict:
    # If no key or SDK missing => heuristic
    if not SETTINGS.openai_key or OpenAI is None:
        return _heuristic(side_hint, last, vola)
    try:
        client = OpenAI(api_key=SETTINGS.openai_key)
        sys = ("Jesteś asystentem-traderem. Masz zwrócić JEDYNIE obiekt JSON (bez komentarzy i bez bloków kodu). "
               "Klucze: action (LONG|SHORT), entry, sl, tp1, tp2, tp3, rr, confidence, success, reason. "
               "Weź pod uwagę podane cechy: FVG_long, FVG_short, RR_coeff, OBI, NEWS, WHALE, ONCHAIN, last, vola_ATR. "
               "Entry/SL/TP muszą być sensowne względem last i vola. Bez żadnych dodatkowych słów.")
        usr = json.dumps({
            "hint_side": side_hint,
            "last": last,
            "vola_ATR": vola,
            "features": {
                "FVG_long": context.get("f_long", 0.5),
                "FVG_short": context.get("f_short", 0.5),
                "RR_coeff": context.get("rr_c", 0.5),
                "OBI": context.get("obi", 0.5),
                "NEWS": context.get("news", 0.5),
                "WHALE": context.get("whale", 0.5),
                "ONCHAIN": context.get("onc", 0.5)
            }
        })
        rsp = client.chat.completions.create(
            model=SETTINGS.openai_model,
            messages=[{"role":"system","content":sys},{"role":"user","content":usr}],
            temperature=0.2,
        )
        txt = rsp.choices[0].message.content.strip()
        data = json.loads(txt)
        # sanity
        action = str(data.get("action", side_hint)).upper()
        entry = float(data.get("entry", last))
        sl = float(data.get("sl", last*(0.99 if action=='LONG' else 1.01)))
        tp1 = float(data.get("tp1", last*(1.01 if action=='LONG' else 0.99)))
        tp2 = float(data.get("tp2", last*(1.02 if action=='LONG' else 0.98)))
        tp3 = float(data.get("tp3", last*(1.04 if action=='LONG' else 0.96)))
        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        rr = (reward / (risk+1e-9)) if risk>0 else float(data.get("rr", 1.2))
        conf = float(data.get("confidence", 0.75))
        succ = float(data.get("success", 0.70))
        reason = str(data.get("reason", "Plan OpenAI"))
        return dict(action=action, entry=entry, sl=sl, tp1=tp1, tp2=tp2, tp3=tp3, rr=rr, confidence=conf, success=succ, reason=reason)
    except Exception:
        return _heuristic(side_hint, last, vola)


        return {
    "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
    "rr": rr, "conf": confidence, "confidence": confidence,
    "success": success_chance, "success_chance": success_chance,
    "reason": reason_text,
}
