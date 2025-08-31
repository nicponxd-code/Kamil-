from typing import Tuple
def fuse_edge(long_fvg: float, short_fvg: float, rr_coeff: float, obi: float,
              news: float, whale: float, onchain: float,
              w_fvg: float, w_rr: float, w_obi: float, w_news: float, w_whale: float, w_onc: float) -> Tuple[float, float]:
    long_edge = (w_fvg*long_fvg + w_rr*rr_coeff + w_obi*obi + w_news*news + w_whale*whale + w_onc*onchain)
    short_edge = (w_fvg*short_fvg + w_rr*(1-rr_coeff) + w_obi*(1-obi) + w_news*(1-news) + w_whale*(1-whale) + w_onc*(1-onchain))
    return long_edge, short_edge
