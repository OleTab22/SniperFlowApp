# backend/microstructure.py
"""
Institutional-lite microstructure engine for order-flow and execution quality signals.
Uses free tick-level bid/ask data (no L2 sizes required).

Key metrics:
- Order Flow Imbalance (OFI): proxy from quote updates
- Microprice: pressure-adjusted fair value
- Variance Ratio: regime classification (mean-reversion vs momentum)
- Spread Hygiene: relative spread caps to avoid adverse selection
"""

from collections import deque
from dataclasses import dataclass
from time import time
import math
from typing import Optional, Dict, Any
import logging

log = logging.getLogger(__name__)


@dataclass
class Tick:
    """Single quote update snapshot."""
    ts: float
    bid: float
    ask: float


class MicroEngine:
    """
    Lightweight microstructure engine for 1Hz tick data.
    
    Features computed:
    - OFI (Order Flow Imbalance): net bid/ask quote pressure
    - Microprice: fair value adjusted for OFI
    - Variance Ratio: short-term vs long-term variance (regime proxy)
    - Quote activity: updates per second
    - Micro-trend: EMA of mid returns
    """
    
    def __init__(self, win_secs: int = 20, ofi_decay: float = 0.95):
        """
        Args:
            win_secs: Rolling window for metrics (default 20s)
            ofi_decay: Optional exponential decay for OFI (0.95 = ~5% per tick)
        """
        self.win_secs = win_secs
        self.ofi_decay = ofi_decay
        self.ticks: deque[Tick] = deque()
        self.last_bid: Optional[float] = None
        self.last_ask: Optional[float] = None
        self.cum_ofi: float = 0.0
        self.last_mid: Optional[float] = None
        self.ema_trend: float = 0.0
        self.alpha: float = 0.3  # EMA smoothing for micro-trend
        
        # Diagnostics
        self.total_ticks: int = 0
        self.signals_generated: int = 0
        
    def on_tick(self, bid: float, ask: float, ts: Optional[float] = None) -> None:
        """
        Process a new quote update.
        
        Args:
            bid: Best bid price
            ask: Best ask price
            ts: Timestamp (seconds since epoch); defaults to now
        """
        ts = ts or time()
        
        # Sanity checks
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask <= bid:
            log.warning(f"Invalid tick: bid={bid}, ask={ask}")
            return
        
        # Maintain rolling window
        self.ticks.append(Tick(ts, bid, ask))
        while self.ticks and ts - self.ticks[0].ts > self.win_secs:
            self.ticks.popleft()
        
        # Update OFI (Order Flow Imbalance)
        # Logic: bid up = buying pressure (+), ask down = buying pressure (+)
        #        bid down = selling pressure (-), ask up = selling pressure (-)
        if self.last_bid is not None and self.last_ask is not None:
            db = 1.0 if bid > self.last_bid else (-1.0 if bid < self.last_bid else 0.0)
            da = 1.0 if ask > self.last_ask else (-1.0 if ask < self.last_ask else 0.0)
            ofi_increment = db - da  # Bid up & ask down = +2 (strong buy pressure)
            
            # Optional: apply decay to older OFI (reduces stale pressure influence)
            self.cum_ofi = self.cum_ofi * self.ofi_decay + ofi_increment
        
        self.last_bid, self.last_ask = bid, ask
        
        # Update micro-trend (EMA of mid returns)
        mid = (bid + ask) / 2.0
        if self.last_mid is not None:
            ret = mid - self.last_mid
            self.ema_trend = self.alpha * ret + (1.0 - self.alpha) * self.ema_trend
        self.last_mid = mid
        
        self.total_ticks += 1
    
    def features(self) -> Optional[Dict[str, Any]]:
        """
        Extract current microstructure features.
        
        Returns:
            Dict with: mid, spread, rel_spread, qps, cum_ofi, ema_trend, vr, microprice
            Returns None if insufficient data.
        """
        if not self.ticks or self.last_bid is None or self.last_ask is None:
            return None
        
        bid, ask = self.last_bid, self.last_ask
        mid = (bid + ask) / 2.0
        spr = ask - bid
        rel_spr = spr / mid if mid > 0 else 0.0
        
        # Quote activity (quotes per second)
        dt = max(1e-6, self.ticks[-1].ts - self.ticks[0].ts)
        qps = len(self.ticks) / dt
        
        # Variance ratio (regime proxy)
        # VR < 1 → mean-reversion bias, VR > 1 → momentum bias
        mids = [(t.bid + t.ask) / 2.0 for t in self.ticks]
        diffs = [mids[i] - mids[i-1] for i in range(1, len(mids))]
        
        if len(diffs) >= 4:
            # Short-term variance (last 4 ticks ~ 4 seconds at 1Hz)
            short_var = sum(d * d for d in diffs[-4:]) / 4
            # Long-term variance (full window)
            long_var = sum(d * d for d in diffs) / len(diffs)
            vr = (long_var + 1e-9) / (short_var + 1e-9)
        else:
            vr = 1.0  # Neutral if insufficient data
        
        # Microprice (pressure-adjusted fair value)
        # If OFI > 0 (buy pressure), microprice skews toward ask
        # If OFI < 0 (sell pressure), microprice skews toward bid
        kappa = 0.7  # Adjustment strength
        W = max(1.0, len(self.ticks))
        pressure = self.cum_ofi / W
        # Use tanh to bound adjustment to [-1, 1]
        adjustment = math.tanh(pressure) * (spr * 0.5) * kappa
        micro = mid + adjustment
        
        return {
            "mid": mid,
            "spread": spr,
            "rel_spread": rel_spr,
            "qps": qps,
            "cum_ofi": self.cum_ofi,
            "ema_trend": self.ema_trend,
            "vr": vr,
            "microprice": micro,
            "bid": bid,
            "ask": ask,
        }
    
    def reset_ofi(self) -> None:
        """Reset cumulative OFI (e.g., after signal generation)."""
        self.cum_ofi = 0.0
    
    def make_signal(
        self,
        pdh: Optional[float] = None,
        pdl: Optional[float] = None,
        atr_5m: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Generate institutional-lite signal based on OFI + microprice + regime.
        
        Logic:
        - LONG: microprice > mid, OFI > 0, variance-ratio < 1 (reversion)
        - SHORT: microprice < mid, OFI < 0, variance-ratio < 1
        - Optional: require sweep of PDL/PDH for higher confidence
        - Quality gates: spread < 3bp, activity > 0.5 qps
        
        Args:
            pdh: Previous Day High (for sweep context)
            pdl: Previous Day Low (for sweep context)
            atr_5m: 5-minute ATR for stop-loss sizing
        
        Returns:
            Signal dict or None if no edge detected
        """
        f = self.features()
        if not f:
            return None
        
        mid = f["mid"]
        spr = f["spread"]
        rel_spr = f["rel_spread"]
        micro = f["microprice"]
        ofi = f["cum_ofi"]
        vr = f["vr"]
        qps = f["qps"]
        bid = f["bid"]
        ask = f["ask"]
        
        # Quality gates
        if rel_spr > 0.0003:  # 3 bps relative spread cap
            return None
        if qps < 0.5:  # Too quiet
            return None
        
        # Sweep context detection (optional boost)
        sweep_long = False
        sweep_short = False
        if pdl is not None and self.ticks:
            lowest_bid = min(t.bid for t in self.ticks)
            sweep_long = lowest_bid <= pdl * 0.999  # Swept below PDL
        if pdh is not None and self.ticks:
            highest_ask = max(t.ask for t in self.ticks)
            sweep_short = highest_ask >= pdh * 1.001  # Swept above PDH
        
        # Reversal logic
        # LONG: buy pressure (OFI > 0) + microprice above mid + reversion regime
        long_ok = (micro > mid) and (ofi > 0) and (vr < 1.0)
        # SHORT: sell pressure (OFI < 0) + microprice below mid + reversion regime
        short_ok = (micro < mid) and (ofi < 0) and (vr < 1.0)
        
        # Stop-loss sizing (fallback to 0.5% of mid if ATR unavailable)
        if atr_5m is None or atr_5m <= 0:
            atr_5m = mid * 0.005
        sl_dist = 1.2 * atr_5m
        
        if long_ok:
            # Entry inside spread to avoid adverse selection
            entry = mid - 0.25 * spr
            sl = entry - sl_dist
            tp1 = entry + 1.6 * sl_dist  # 1.6R
            tp2 = entry + 2.5 * sl_dist  # 2.5R
            
            confidence = 0.80 if sweep_long else 0.65
            reason = f"OFI={ofi:.1f}+ μPrice>{mid:.2f} reversion VR={vr:.2f} spread={int(rel_spr*10000)}bp"
            if sweep_long:
                reason += " +SWEEP_PDL"
            
            self.signals_generated += 1
            return {
                "side": "LONG",
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp1": round(tp1, 2),
                "tp2": round(tp2, 2),
                "confidence": confidence,
                "reason": reason,
                "regime": "MICRO-REVERSAL",
                "ofi": round(ofi, 2),
                "vr": round(vr, 3),
                "spread_bp": int(rel_spr * 10000),
                "swept_pdl": sweep_long,
            }
        
        if short_ok:
            # Entry inside spread
            entry = mid + 0.25 * spr
            sl = entry + sl_dist
            tp1 = entry - 1.6 * sl_dist
            tp2 = entry - 2.5 * sl_dist
            
            confidence = 0.80 if sweep_short else 0.65
            reason = f"OFI={ofi:.1f}- μPrice<{mid:.2f} reversion VR={vr:.2f} spread={int(rel_spr*10000)}bp"
            if sweep_short:
                reason += " +SWEEP_PDH"
            
            self.signals_generated += 1
            return {
                "side": "SHORT",
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp1": round(tp1, 2),
                "tp2": round(tp2, 2),
                "confidence": confidence,
                "reason": reason,
                "regime": "MICRO-REVERSAL",
                "ofi": round(ofi, 2),
                "vr": round(vr, 3),
                "spread_bp": int(rel_spr * 10000),
                "swept_pdh": sweep_short,
            }
        
        return None
    
    def diagnostics(self) -> Dict[str, Any]:
        """Return engine diagnostics."""
        return {
            "total_ticks": self.total_ticks,
            "window_ticks": len(self.ticks),
            "cum_ofi": round(self.cum_ofi, 2),
            "ema_trend": round(self.ema_trend, 4),
            "signals_generated": self.signals_generated,
        }

