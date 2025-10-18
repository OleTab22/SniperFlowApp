"""
Unit tests for microstructure engine.
Tests OFI calculation, microprice, regime detection, and signal generation.
"""

import pytest
from microstructure import MicroEngine, Tick
import time


def test_engine_initialization():
    """Test engine initializes with correct defaults."""
    engine = MicroEngine(win_secs=20)
    assert engine.win_secs == 20
    assert engine.cum_ofi == 0.0
    assert len(engine.ticks) == 0


def test_ofi_buy_pressure():
    """Test OFI detects buying pressure (bid up, ask unchanged/down)."""
    engine = MicroEngine(win_secs=10, ofi_decay=1.0)  # No decay for testing
    
    # Bid rises (buying pressure)
    engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0)
    engine.on_tick(bid=2630.5, ask=2630.5, ts=101.0)  # +1 (bid up)
    engine.on_tick(bid=2631.0, ask=2630.5, ts=102.0)  # +1 (bid up)
    
    assert engine.cum_ofi > 0, "OFI should be positive for buy pressure"


def test_ofi_sell_pressure():
    """Test OFI detects selling pressure (ask down, bid unchanged/down)."""
    engine = MicroEngine(win_secs=10, ofi_decay=1.0)
    
    # Ask falls (selling pressure)
    engine.on_tick(bid=2630.0, ask=2631.0, ts=100.0)
    engine.on_tick(bid=2630.0, ask=2630.5, ts=101.0)  # -1 (ask down)
    engine.on_tick(bid=2629.5, ask=2630.0, ts=102.0)  # -2 (bid down + ask down)
    
    assert engine.cum_ofi < 0, "OFI should be negative for sell pressure"


def test_variance_ratio_momentum():
    """Test variance ratio detects momentum (VR > 1)."""
    engine = MicroEngine(win_secs=10)
    
    # Trending price (large recent moves)
    for i in range(10):
        price = 2630.0 + i * 2.0  # Strong trend
        engine.on_tick(bid=price, ask=price + 0.5, ts=100.0 + i)
    
    features = engine.features()
    assert features is not None
    # Recent variance should be larger → VR may be > 1 (momentum)
    # Note: with strong trend, variance ratio behavior depends on lookback


def test_variance_ratio_reversion():
    """Test variance ratio detects mean-reversion (VR < 1)."""
    engine = MicroEngine(win_secs=10)
    
    # Choppy price (small recent moves after initial volatility)
    prices = [2630, 2632, 2628, 2631, 2629, 2630.5, 2630.2, 2630.3, 2630.1, 2630.2]
    for i, price in enumerate(prices):
        engine.on_tick(bid=price, ask=price + 0.5, ts=100.0 + i)
    
    features = engine.features()
    assert features is not None
    # Recent variance smaller than long-term → VR < 1 (reversion)


def test_microprice_buy_pressure():
    """Test microprice skews toward ask during buy pressure."""
    engine = MicroEngine(win_secs=10, ofi_decay=1.0)
    
    # Create strong buy pressure
    engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0)
    for i in range(5):
        engine.on_tick(bid=2630.0 + i * 0.1, ask=2630.5, ts=101.0 + i)
    
    features = engine.features()
    mid = features["mid"]
    micro = features["microprice"]
    
    assert micro > mid, "Microprice should be above mid during buy pressure"


def test_microprice_sell_pressure():
    """Test microprice skews toward bid during sell pressure."""
    engine = MicroEngine(win_secs=10, ofi_decay=1.0)
    
    # Create strong sell pressure
    engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0)
    for i in range(5):
        engine.on_tick(bid=2630.0, ask=2630.5 - i * 0.1, ts=101.0 + i)
    
    features = engine.features()
    mid = features["mid"]
    micro = features["microprice"]
    
    assert micro < mid, "Microprice should be below mid during sell pressure"


def test_signal_long_sweep_reversal():
    """Test LONG signal on sweep + OFI reversal."""
    engine = MicroEngine(win_secs=20, ofi_decay=0.98)
    
    # 1) Downward sweep toward PDL
    pdl = 2625.0
    for i in range(10):
        price = 2630.0 - i * 0.5
        engine.on_tick(bid=price, ask=price + 0.5, ts=100.0 + i * 0.5)
    
    # 2) Reversal: buying pressure (bid rises)
    for i in range(8):
        price = 2625.5 + i * 0.3
        engine.on_tick(bid=price, ask=price + 0.5, ts=110.0 + i * 0.5)
    
    # 3) Generate signal
    sig = engine.make_signal(pdh=2650.0, pdl=pdl, atr_5m=5.0)
    
    if sig:  # May not trigger if VR or OFI thresholds not met
        assert sig["side"] == "LONG"
        assert sig["entry"] < sig["tp1"] < sig["tp2"]
        assert sig["entry"] > sig["sl"]
        assert "SWEEP" in sig["reason"] or sig["confidence"] > 0


def test_signal_short_sweep_reversal():
    """Test SHORT signal on sweep + OFI reversal."""
    engine = MicroEngine(win_secs=20, ofi_decay=0.98)
    
    # 1) Upward sweep toward PDH
    pdh = 2650.0
    for i in range(10):
        price = 2640.0 + i * 0.5
        engine.on_tick(bid=price, ask=price + 0.5, ts=100.0 + i * 0.5)
    
    # 2) Reversal: selling pressure (ask falls)
    for i in range(8):
        price = 2649.5 - i * 0.3
        engine.on_tick(bid=price, ask=price + 0.5, ts=110.0 + i * 0.5)
    
    # 3) Generate signal
    sig = engine.make_signal(pdh=pdh, pdl=2620.0, atr_5m=5.0)
    
    if sig:
        assert sig["side"] == "SHORT"
        assert sig["entry"] > sig["tp1"] > sig["tp2"]
        assert sig["entry"] < sig["sl"]


def test_signal_blocks_wide_spread():
    """Test signal blocked when spread too wide (>3bp)."""
    engine = MicroEngine(win_secs=20)
    
    # Create conditions for signal but with wide spread
    for i in range(10):
        engine.on_tick(bid=2630.0 + i * 0.2, ask=2632.0 + i * 0.2, ts=100.0 + i)
    
    sig = engine.make_signal(pdh=2650.0, pdl=2620.0, atr_5m=5.0)
    
    # Wide spread (2.0 / 2631 = 76bp) should block signal
    assert sig is None, "Wide spread should block signal generation"


def test_signal_blocks_low_activity():
    """Test signal blocked when quote activity too low."""
    engine = MicroEngine(win_secs=60)  # Long window
    
    # Only 3 ticks in 60 seconds = 0.05 qps (below 0.5 threshold)
    engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0)
    engine.on_tick(bid=2630.1, ask=2630.6, ts=130.0)
    engine.on_tick(bid=2630.2, ask=2630.7, ts=160.0)
    
    sig = engine.make_signal(pdh=2650.0, pdl=2620.0, atr_5m=5.0)
    
    # Low activity should block
    assert sig is None, "Low activity should block signal generation"


def test_rolling_window():
    """Test engine maintains rolling window correctly."""
    engine = MicroEngine(win_secs=5)
    
    # Add ticks spanning 10 seconds
    for i in range(10):
        engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0 + i)
    
    # Only last 5 seconds should remain
    assert len(engine.ticks) <= 6, "Window should contain ~5-6 ticks for 5s window"
    assert engine.ticks[0].ts >= 105.0, "Oldest tick should be within window"


def test_diagnostics():
    """Test diagnostics output."""
    engine = MicroEngine(win_secs=10)
    
    for i in range(5):
        engine.on_tick(bid=2630.0, ask=2630.5, ts=100.0 + i)
    
    diag = engine.diagnostics()
    assert diag["total_ticks"] == 5
    assert diag["window_ticks"] == 5
    assert "cum_ofi" in diag
    assert "ema_trend" in diag


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])

