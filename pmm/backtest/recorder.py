import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from pmm.config import PMMConfig
from pmm.market_ws import MarketWsFeed

logger = logging.getLogger(__name__)


class LiveRecorder:
    def __init__(self, token_ids: List[str], interval: float = 1.0):
        self.token_ids = token_ids
        self.interval = interval
        self.config = PMMConfig.from_env()
        self.ws_feed = MarketWsFeed(self.config, token_ids)
        self.ticks: List[Dict[str, Any]] = []
        self._running = False

    async def run(self, duration_sec: int = 60, output_file: str = "recorded_scenario.json"):
        print(f"Starting recorder for {len(self.token_ids)} tokens...")
        print(f"Duration: {duration_sec}s, Interval: {self.interval}s")
        
        # Start WebSocket
        feed_task = asyncio.create_task(self.ws_feed.run())
        
        # Wait for initial data
        print("Waiting for initial data (5s warm-up)...")
        await asyncio.sleep(5)
        
        start_time = time.time()
        tick_count = 0
        self._running = True
        
        try:
            while self._running:
                now = time.time()
                elapsed = now - start_time
                
                if duration_sec > 0 and elapsed >= duration_sec:
                    break
                
                # Capture snapshot
                snapshot = self._capture_snapshot(tick_count)
                self.ticks.append(snapshot)
                
                tick_count += 1
                if tick_count % 10 == 0:
                    print(f"Recorded {tick_count} ticks ({elapsed:.1f}s elapsed)")
                
                # Wait for next tick
                next_tick = start_time + (tick_count + 1) * self.interval
                sleep_time = max(0, next_tick - time.time())
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            print("\nRecording stopped by user.")
        finally:
            self._running = False
            self.ws_feed.stop()
            # Cancel feed task but ignore cancellation error
            feed_task.cancel()
            try:
                await feed_task
            except asyncio.CancelledError:
                pass
            
            self._save(output_file, start_time)

    def _capture_snapshot(self, t: int) -> Dict[str, Any]:
        """Capture current orderbook state in scenario format."""
        orderbooks = {}
        
        for token_id in self.token_ids:
            ob = self.ws_feed.get_orderbook(token_id)
            if not ob:
                # Empty book fallback
                orderbooks[token_id] = {"bids": [], "asks": []}
                continue
                
            # Convert to scenario format (list of dicts)
            # MarketWsFeed returns {bids: [(px, sz), ...], asks: ...}
            # Scenario expects {bids: [{"price": px, "size": sz}, ...]}
            
            bids = [{"price": float(px), "size": float(sz)} for px, sz in ob.bids[:10]]
            asks = [{"price": float(px), "size": float(sz)} for px, sz in ob.asks[:10]]
            
            orderbooks[token_id] = {
                "bids": bids,
                "asks": asks
            }
            
        return {
            "t": t,
            "event": "real_data",
            "ts": time.time(),
            "orderbooks": orderbooks
        }

    def _save(self, filename: str, start_ts: float):
        """Save recorded data to JSON."""
        # Calculate derived metrics for scenario spec
        if not self.ticks:
            print("No data recorded.")
            return

        print(f"\nSaving {len(self.ticks)} ticks to {filename}...")
        
        # Estimate base_mid from average of first tick
        first_tick = self.ticks[0]
        base_mid = 0.5
        try:
            yes_id = self.token_ids[0]
            ob = first_tick["orderbooks"][yes_id]
            if ob["bids"] and ob["asks"]:
                base_mid = (ob["bids"][0]["price"] + ob["asks"][0]["price"]) / 2
        except Exception:
            pass

        scenario_data = {
            "scenario_id": Path(filename).stem,
            "description": f"Live recording from {datetime.fromtimestamp(start_ts).isoformat()}",
            "token_ids": self.token_ids,
            "initial_state": {
                "usdc": 1000.0,
                "positions": {tid: 0.0 for tid in self.token_ids}
            },
            # Default compliant strategy config
            "strategy_overrides": {
                "base_spread": 0.02,
                "min_profitability_spread": 0.005,
                "paper_fill_model": "optimistic",  # Use optimistic for real data replay? Or BBO-join?
                # Actually, replay_runner logic applies. 
                # Real data has real spread, might be tight.
                "price_tick": 0.01  # Assuming standard market
            },
            "meta": {
                "source": "live_recorder",
                "interval": self.interval,
                "start_time": start_ts,
                "end_time": time.time()
            },
            "ticks": self.ticks
        }
        
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        with open(filename, "w") as f:
            json.dump(scenario_data, f, indent=2)
            
        print(f"Saved successfully: {filename}")
