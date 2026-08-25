"""
Portfolio Manager with SQLite backend.
Tracks positions across multiple bots and enforces portfolio-level risk limits.
"""

import sqlite3
import logging
from datetime import datetime, date
from typing import Dict, List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class PortfolioConfig:
    """Portfolio risk limits."""
    MAX_POSITIONS = 4
    MAX_CURRENCY_EXPOSURE = 2  # max 2 positions with same base currency
    MAX_CORRELATED_POSITIONS = 2  # max 2 positions with correlation > 0.7
    MAX_DAILY_LOSS = -200.0  # USD
    MAX_MARGIN_USAGE_PCT = 50.0  # % of account
    
    # Correlation pairs (simplified - can be expanded)
    HIGH_CORRELATION_PAIRS = {
        ('EURUSD', 'GBPUSD'): 0.8,
        ('EURUSD', 'AUDUSD'): 0.7,
        ('GBPUSD', 'AUDUSD'): 0.7,
        ('USDJPY', 'USDCAD'): 0.6,
        ('XAUUSD', 'EURUSD'): 0.5,
    }


class PortfolioManager:
    """Manages portfolio-level risk across multiple trading bots."""
    
    def __init__(self, db_path: str = "portfolio.db"):
        self.db_path = Path(db_path)
        self.config = PortfolioConfig()
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with schema."""
        conn = sqlite3.connect(self.db_path)
        
        # Positions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                volume REAL NOT NULL,
                entry_price REAL NOT NULL,
                sl_pips REAL,
                tp_pips REAL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Daily stats table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                loss_streak INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id)")
        
        conn.commit()
        conn.close()
        logger.info(f"Portfolio database initialized at {self.db_path}")
    
    def _get_conn(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)
    
    def register_position(self, bot_id: str, symbol: str, side: str, 
                         volume: float, entry_price: float, 
                         sl_pips: float, tp_pips: float) -> bool:
        """Register new position after trade execution."""
        try:
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO positions (bot_id, symbol, side, volume, entry_price, 
                                     sl_pips, tp_pips, entry_time, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open')
            """, (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips))
            conn.commit()
            conn.close()
            logger.info(f"Position registered: {symbol} {side} {volume} lots by {bot_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register position: {e}")
            return False
    
    def close_position(self, bot_id: str, symbol: str, exit_price: float, pnl: float) -> bool:
        """Mark position as closed and update daily stats."""
        try:
            conn = self._get_conn()
            
            # Update position
            conn.execute("""
                UPDATE positions 
                SET status = 'closed', exit_price = ?, pnl = ?, exit_time = datetime('now')
                WHERE bot_id = ? AND symbol = ? AND status = 'open'
            """, (exit_price, pnl, bot_id, symbol))
            
            # Update daily stats
            today = date.today().isoformat()
            conn.execute("""
                INSERT INTO daily_stats (date, total_pnl, trades_count, loss_streak)
                VALUES (?, ?, 1, CASE WHEN ? < 0 THEN 1 ELSE 0 END)
                ON CONFLICT(date) DO UPDATE SET
                    total_pnl = total_pnl + ?,
                    trades_count = trades_count + 1,
                    loss_streak = CASE 
                        WHEN ? < 0 THEN loss_streak + 1
                        ELSE 0
                    END,
                    updated_at = datetime('now')
            """, (today, pnl, pnl, pnl, pnl))
            
            conn.commit()
            conn.close()
            logger.info(f"Position closed: {symbol} by {bot_id}, PnL: {pnl}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False
    
    def check_risk(self, symbol: str, side: str, volume: float, sl_pips: float,
                   account_balance: float = 10000.0) -> Tuple[bool, str]:
        """
        Check if new trade is safe at portfolio level.
        Returns (allowed: bool, reason: str)
        """
        try:
            conn = self._get_conn()
            
            # 1. Max positions check
            cursor = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'open'"
            )
            open_positions = cursor.fetchone()[0]
            if open_positions >= self.config.MAX_POSITIONS:
                conn.close()
                return False, f"Max positions reached ({self.config.MAX_POSITIONS})"
            
            # 2. Currency exposure check
            base_currency = symbol[:3]
            cursor = conn.execute("""
                SELECT COUNT(*) FROM positions 
                WHERE status = 'open' AND (symbol LIKE ? OR symbol LIKE ?)
            """, (f"{base_currency}%", f"%{base_currency}"))
            currency_count = cursor.fetchone()[0]
            if currency_count >= self.config.MAX_CURRENCY_EXPOSURE:
                conn.close()
                return False, f"Max {base_currency} exposure ({self.config.MAX_CURRENCY_EXPOSURE})"
            
            # 3. Correlation check
            for existing_symbol, _ in self._get_open_symbols(conn):
                if self._is_highly_correlated(symbol, existing_symbol):
                    cursor = conn.execute("""
                        SELECT COUNT(*) FROM positions 
                        WHERE status = 'open' AND symbol = ?
                    """, (existing_symbol,))
                    if cursor.fetchone()[0] >= self.config.MAX_CORRELATED_POSITIONS:
                        conn.close()
                        return False, f"High correlation with {existing_symbol}"
            
            # 4. Daily loss limit
            today = date.today().isoformat()
            cursor = conn.execute(
                "SELECT total_pnl, loss_streak FROM daily_stats WHERE date = ?",
                (today,)
            )
            row = cursor.fetchone()
            if row:
                daily_pnl, loss_streak = row
                if daily_pnl <= self.config.MAX_DAILY_LOSS:
                    conn.close()
                    return False, f"Daily loss limit reached ({daily_pnl:.2f})"
                if loss_streak >= 3:
                    conn.close()
                    return False, f"Loss streak too high ({loss_streak})"
            
            # 5. Margin usage estimate (simplified)
            cursor = conn.execute("""
                SELECT SUM(volume) FROM positions WHERE status = 'open'
            """)
            total_volume = cursor.fetchone()[0] or 0
            estimated_margin = (total_volume + volume) * 1000  # rough estimate
            margin_pct = (estimated_margin / account_balance) * 100
            if margin_pct > self.config.MAX_MARGIN_USAGE_PCT:
                conn.close()
                return False, f"Margin usage too high ({margin_pct:.1f}%)"
            
            conn.close()
            return True, "OK"
            
        except Exception as e:
            logger.error(f"Risk check failed: {e}")
            return False, f"Risk check error: {e}"
    
    def _get_open_symbols(self, conn) -> List[Tuple[str, str]]:
        """Get list of (symbol, side) for open positions."""
        cursor = conn.execute("""
            SELECT symbol, side FROM positions WHERE status = 'open'
        """)
        return cursor.fetchall()
    
    def _is_highly_correlated(self, symbol1: str, symbol2: str) -> bool:
        """Check if two symbols have high correlation."""
        if symbol1 == symbol2:
            return False
        
        # Check both orderings
        pair1 = (symbol1, symbol2)
        pair2 = (symbol2, symbol1)
        
        correlation = self.config.HIGH_CORRELATION_PAIRS.get(
            pair1, self.config.HIGH_CORRELATION_PAIRS.get(pair2, 0)
        )
        
        return correlation >= 0.7
    
    def get_portfolio_status(self) -> Dict:
        """Get current portfolio status."""
        try:
            conn = self._get_conn()
            
            # Open positions
            cursor = conn.execute("""
                SELECT bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, entry_time
                FROM positions WHERE status = 'open'
                ORDER BY entry_time DESC
            """)
            open_positions = [
                {
                    "bot_id": row[0],
                    "symbol": row[1],
                    "side": row[2],
                    "volume": row[3],
                    "entry_price": row[4],
                    "sl_pips": row[5],
                    "tp_pips": row[6],
                    "entry_time": row[7]
                }
                for row in cursor.fetchall()
            ]
            
            # Daily stats
            today = date.today().isoformat()
            cursor = conn.execute(
                "SELECT total_pnl, trades_count, loss_streak FROM daily_stats WHERE date = ?",
                (today,)
            )
            row = cursor.fetchone()
            daily_stats = {
                "date": today,
                "total_pnl": row[0] if row else 0,
                "trades_count": row[1] if row else 0,
                "loss_streak": row[2] if row else 0
            } if row else {"date": today, "total_pnl": 0, "trades_count": 0, "loss_streak": 0}
            
            # Currency exposure
            cursor = conn.execute("""
                SELECT 
                    CASE 
                        WHEN symbol LIKE 'EUR%' THEN 'EUR'
                        WHEN symbol LIKE 'USD%' THEN 'USD'
                        WHEN symbol LIKE 'GBP%' THEN 'GBP'
                        WHEN symbol LIKE 'JPY%' THEN 'JPY'
                        WHEN symbol LIKE 'AUD%' THEN 'AUD'
                        WHEN symbol LIKE 'CAD%' THEN 'CAD'
                        WHEN symbol LIKE 'CHF%' THEN 'CHF'
                        WHEN symbol LIKE 'XAU%' THEN 'XAU'
                        ELSE 'OTHER'
                    END as currency,
                    COUNT(*) as count
                FROM positions 
                WHERE status = 'open'
                GROUP BY currency
            """)
            currency_exposure = {row[0]: row[1] for row in cursor.fetchall()}
            
            conn.close()
            
            return {
                "open_positions": open_positions,
                "daily_stats": daily_stats,
                "currency_exposure": currency_exposure,
                "total_positions": len(open_positions)
            }
            
        except Exception as e:
            logger.error(f"Failed to get portfolio status: {e}")
            return {
                "open_positions": [],
                "daily_stats": {"date": today, "total_pnl": 0, "trades_count": 0, "loss_streak": 0},
                "currency_exposure": {},
                "total_positions": 0,
                "error": str(e)
            }
    
    def get_position_count(self, symbol: Optional[str] = None) -> int:
        """Get count of open positions, optionally filtered by symbol."""
        try:
            conn = self._get_conn()
            if symbol:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM positions WHERE status = 'open' AND symbol = ?",
                    (symbol,)
                )
            else:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM positions WHERE status = 'open'"
                )
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            logger.error(f"Failed to get position count: {e}")
            return 0


# Global instance (will be initialized in server.py)
portfolio_manager: Optional[PortfolioManager] = None


def init_portfolio(db_path: str = "portfolio.db") -> PortfolioManager:
    """Initialize global portfolio manager instance."""
    global portfolio_manager
    portfolio_manager = PortfolioManager(db_path)
    return portfolio_manager


def get_portfolio_manager() -> PortfolioManager:
    """Get global portfolio manager instance."""
    if portfolio_manager is None:
        raise RuntimeError("Portfolio manager not initialized. Call init_portfolio() first.")
    return portfolio_manager
