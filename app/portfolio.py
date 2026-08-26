"""
Portfolio Manager with SQLite backend.
Tracks positions across multiple bots and enforces portfolio-level risk limits.
"""

import sqlite3
import logging
from datetime import datetime, date
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from app.accounts import get_account_registry

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
        
        # Setup accounts table
        registry = get_account_registry()
        registry._init_schema()

        # Migrate positions table
        try:
            conn.execute("ALTER TABLE positions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'default'")
        except sqlite3.OperationalError:
            pass # column exists

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
                account_id TEXT NOT NULL DEFAULT 'default',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Daily stats table migration
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats_v2 (
                account_id TEXT NOT NULL,
                date TEXT NOT NULL,
                total_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                loss_streak INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, date)
            )
        """)
        
        # Copy legacy rows if daily_stats exists
        try:
            conn.execute("""
                INSERT OR IGNORE INTO daily_stats_v2 (account_id, date, total_pnl, trades_count, loss_streak, updated_at) 
                SELECT 'default', date, total_pnl, trades_count, loss_streak, updated_at FROM daily_stats
            """)
            conn.execute("DROP TABLE daily_stats")
            conn.execute("ALTER TABLE daily_stats_v2 RENAME TO daily_stats")
        except sqlite3.OperationalError:
            # table might not exist, or already renamed
            pass

        # Create normal daily_stats table if it doesn't exist (if not handled by rename)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                account_id TEXT NOT NULL,
                date TEXT NOT NULL,
                total_pnl REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                loss_streak INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (account_id, date)
            )
        """)

        # Create indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_id ON positions(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_id, status)")

        # Cbot Configs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cbot_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                run_command TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Portfolio database initialized at {self.db_path}")
    
    def _get_conn(self):
        """Get database connection."""
        return sqlite3.connect(self.db_path)
    
    def register_position(self, bot_id: str, symbol: str, side: str, 
                         volume: float, entry_price: float, 
                         sl_pips: float, tp_pips: float, account_id: str) -> bool:
        """Register new position after trade execution."""
        try:
            conn = self._get_conn()
            conn.execute("""
                INSERT INTO positions (bot_id, symbol, side, volume, entry_price, 
                                     sl_pips, tp_pips, entry_time, status, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open', ?)
            """, (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, account_id))
            conn.commit()
            conn.close()
            logger.info(f"Position registered: {symbol} {side} {volume} lots by {bot_id} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register position: {e}")
            return False
    
    def close_position(self, bot_id: str, symbol: str, exit_price: float, pnl: float, account_id: str) -> bool:
        """Mark position as closed and update daily stats."""
        try:
            conn = self._get_conn()
            
            # Update position
            conn.execute("""
                UPDATE positions 
                SET status = 'closed', exit_price = ?, pnl = ?, exit_time = datetime('now')
                WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
            """, (exit_price, pnl, bot_id, symbol, account_id))
            
            # Update daily stats
            today = date.today().isoformat()
            conn.execute("""
                INSERT INTO daily_stats (account_id, date, total_pnl, trades_count, loss_streak)
                VALUES (?, ?, ?, 1, CASE WHEN ? < 0 THEN 1 ELSE 0 END)
                ON CONFLICT(account_id, date) DO UPDATE SET
                    total_pnl = total_pnl + ?,
                    trades_count = trades_count + 1,
                    loss_streak = CASE 
                        WHEN ? < 0 THEN loss_streak + 1
                        ELSE 0
                    END,
                    updated_at = datetime('now')
            """, (account_id, today, pnl, pnl, pnl, pnl))
            
            conn.commit()
            conn.close()
            logger.info(f"Position closed: {symbol} by {bot_id}, PnL: {pnl} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False
    
    def check_risk(self, symbol: str, side: str, volume: float, sl_pips: float,
                   account_balance: float = 10000.0, account_id: str = "default") -> Tuple[bool, str]:
        """
        Check if new trade is safe at portfolio level.
        Returns (allowed: bool, reason: str)
        """
        try:
            conn = self._get_conn()
            
            # 1. Max positions check
            cursor = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'open' AND account_id = ?", (account_id,)
            )
            open_positions = cursor.fetchone()[0]
            if open_positions >= self.config.MAX_POSITIONS:
                conn.close()
                return False, f"Max positions reached ({self.config.MAX_POSITIONS})"
            
            # 2. Currency exposure check
            base_currency = symbol[:3]
            cursor = conn.execute("""
                SELECT COUNT(*) FROM positions 
                WHERE status = 'open' AND (symbol LIKE ? OR symbol LIKE ?) AND account_id = ?
            """, (f"{base_currency}%", f"%{base_currency}", account_id))
            currency_count = cursor.fetchone()[0]
            if currency_count >= self.config.MAX_CURRENCY_EXPOSURE:
                conn.close()
                return False, f"Max {base_currency} exposure ({self.config.MAX_CURRENCY_EXPOSURE})"
            
            # 3. Correlation check
            for existing_symbol, _ in self._get_open_symbols(conn, account_id):
                if self._is_highly_correlated(symbol, existing_symbol):
                    cursor = conn.execute("""
                        SELECT COUNT(*) FROM positions 
                        WHERE status = 'open' AND symbol = ? AND account_id = ?
                    """, (existing_symbol, account_id))
                    if cursor.fetchone()[0] >= self.config.MAX_CORRELATED_POSITIONS:
                        conn.close()
                        return False, f"High correlation with {existing_symbol}"
            
            # 4. Daily loss limit
            today = date.today().isoformat()
            cursor = conn.execute(
                "SELECT total_pnl, loss_streak FROM daily_stats WHERE date = ? AND account_id = ?",
                (today, account_id)
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
                SELECT SUM(volume) FROM positions WHERE status = 'open' AND account_id = ?
            """, (account_id,))
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
    
    def _get_open_symbols(self, conn, account_id: str) -> List[Tuple[str, str]]:
        """Get list of (symbol, side) for open positions."""
        cursor = conn.execute("""
            SELECT symbol, side FROM positions WHERE status = 'open' AND account_id = ?
        """, (account_id,))
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
    
    def get_portfolio_status(self, account_id: Optional[str] = None) -> Dict:
        """Get current portfolio status."""
        try:
            conn = self._get_conn()
            
            # Open positions
            query = """
                SELECT bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, entry_time
                FROM positions WHERE status = 'open'
            """
            params = []
            if account_id and account_id != "all":
                query += " AND account_id = ?"
                params.append(account_id)
            query += " ORDER BY entry_time DESC"
            cursor = conn.execute(query, tuple(params))
            
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
            query = "SELECT SUM(total_pnl), SUM(trades_count), MAX(loss_streak) FROM daily_stats WHERE date = ?"
            params = [today]
            if account_id and account_id != "all":
                query += " AND account_id = ?"
                params.append(account_id)
            cursor = conn.execute(query, tuple(params))
            
            row = cursor.fetchone()
            daily_stats = {
                "date": today,
                "total_pnl": row[0] if row and row[0] is not None else 0,
                "trades_count": row[1] if row and row[1] is not None else 0,
                "loss_streak": row[2] if row and row[2] is not None else 0
            }
            
            # Currency exposure
            query = """
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
            """
            params = []
            if account_id and account_id != "all":
                query += " AND account_id = ?"
                params.append(account_id)
            query += " GROUP BY currency"
            cursor = conn.execute(query, tuple(params))
            
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
                "daily_stats": {"date": date.today().isoformat(), "total_pnl": 0, "trades_count": 0, "loss_streak": 0},
                "currency_exposure": {},
                "total_positions": 0,
                "error": str(e)
            }
    
    def get_position_count(self, symbol: Optional[str] = None, account_id: Optional[str] = None) -> int:
        """Get count of open positions, optionally filtered by symbol."""
        try:
            conn = self._get_conn()
            query = "SELECT COUNT(*) FROM positions WHERE status = 'open'"
            params = []
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            if account_id and account_id != "all":
                query += " AND account_id = ?"
                params.append(account_id)
                
            cursor = conn.execute(query, tuple(params))
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            logger.error(f"Failed to get position count: {e}")
            return 0
    # --- Cbot Config Management ---
    
    def get_cbot_configs(self) -> List[Dict]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, description, run_command, created_at FROM cbot_configs ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "run_command": row[3],
                    "created_at": row[4]
                }
                for row in rows
            ]
        finally:
            conn.close()
            
    def get_cbot_config(self, name: str) -> Optional[Dict]:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, description, run_command, created_at FROM cbot_configs WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "run_command": row[3],
                    "created_at": row[4]
                }
            return None
        finally:
            conn.close()

    def add_cbot_config(self, name: str, description: str, run_command: str) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO cbot_configs (name, description, run_command) VALUES (?, ?, ?)",
                (name, description, run_command)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # Name already exists
        finally:
            conn.close()
            
    def delete_cbot_config(self, name: str) -> bool:
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cbot_configs WHERE name = ?", (name,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


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
