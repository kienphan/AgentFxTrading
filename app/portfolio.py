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
    MAX_DAILY_LOSS = -200.0  # USD
    MAX_MARGIN_USAGE_PCT = 50.0  # % of account
    


class PortfolioManager:
    """Manages portfolio-level risk across multiple trading bots."""
    
    def __init__(self, db_path: str = "portfolio.db"):
        self.db_path = Path(db_path)
        self.config = PortfolioConfig()
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database with schema."""
        conn = sqlite3.connect(self.db_path)
        # WAL mode + busy timeout: 11 bots report concurrently; without these
        # concurrent writes throw "database is locked".
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        
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
        
        # Dynamic daily_stats VIEW migration (Single Source of Truth from positions)
        try:
            cursor = conn.execute("SELECT type FROM sqlite_master WHERE name = 'daily_stats'")
            row = cursor.fetchone()
            if row and row[0] == 'table':
                conn.execute("DROP TABLE daily_stats")
        except Exception:
            pass
        try:
            conn.execute("DROP TABLE IF EXISTS daily_stats_v2")
        except Exception:
            pass

        # Create automatic dynamic view for daily_stats
        conn.execute("""
            CREATE VIEW IF NOT EXISTS daily_stats AS
            SELECT 
                account_id,
                bot_id,
                DATE(COALESCE(exit_time, entry_time)) as date,
                ROUND(SUM(pnl), 2) as total_pnl,
                COUNT(*) as trades_count,
                0 as loss_streak,
                MAX(COALESCE(exit_time, entry_time)) as updated_at
            FROM positions
            WHERE status = 'closed'
            GROUP BY account_id, bot_id, DATE(COALESCE(exit_time, entry_time))
        """)
        # Create indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_id ON positions(account_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_exit_time ON positions(exit_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_entry_time ON positions(entry_time)")
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
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def register_position(self, bot_id: str, symbol: str, side: str, 
                         volume: float, entry_price: float, 
                         sl_pips: float, tp_pips: float, account_id: str) -> bool:
        """Register new position after trade execution."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO positions (bot_id, symbol, side, volume, entry_price, 
                                     sl_pips, tp_pips, entry_time, status, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open', ?)
            """, (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, account_id))
            conn.commit()
            logger.info(f"Position registered: {symbol} {side} {volume} lots by {bot_id} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register position: {e}")
            return False
        finally:
            conn.close()
    def close_position(self, bot_id: str, symbol: str, exit_price: float, pnl: float, account_id: str) -> bool:
        """Mark position as closed (single source of truth)."""
        conn = self._get_conn()
        try:
            # Update position (daily_stats view automatically updates)
            conn.execute("""
                UPDATE positions 
                SET status = 'closed', exit_price = ?, pnl = ?, exit_time = datetime('now')
                WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
            """, (exit_price, pnl, bot_id, symbol, account_id))
            
            conn.commit()
            logger.info(f"Position closed: {symbol} by {bot_id}, PnL: {pnl} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False
        finally:
            conn.close()
    def check_risk(self, symbol: str, side: str, volume: float,
                   account_balance: float = 10000.0, account_id: str = "default") -> Tuple[bool, str]:
        """
        Check if new trade is safe at portfolio level.
        Returns (allowed: bool, reason: str)
        """
        conn = self._get_conn()
        try:
            # 4. Daily loss limit
            today = date.today().isoformat()
            cursor = conn.execute(
                "SELECT SUM(pnl) FROM positions WHERE status = 'closed' AND DATE(COALESCE(exit_time, entry_time)) = ? AND account_id = ?",
                (today, account_id)
            )
            row = cursor.fetchone()
            if row and row[0] is not None:
                daily_pnl = row[0]
                if daily_pnl <= self.config.MAX_DAILY_LOSS:
                    return False, f"Daily loss limit reached ({daily_pnl:.2f})"
            # 5. Margin usage estimate (simplified)
            cursor = conn.execute("""
                SELECT SUM(volume) FROM positions WHERE status = 'open' AND account_id = ?
            """, (account_id,))
            total_volume = cursor.fetchone()[0] or 0
            estimated_margin = (total_volume + volume) * 1000  # rough estimate
            margin_pct = (estimated_margin / account_balance) * 100
            if margin_pct > self.config.MAX_MARGIN_USAGE_PCT:
                return False, f"Margin usage too high ({margin_pct:.1f}%)"
            
            return True, "OK"
        except Exception as e:
            logger.error(f"Risk check failed: {e}")
            return False, f"Risk check error: {e}"
        finally:
            conn.close()
    def _get_open_symbols(self, conn, account_id: str) -> List[Tuple[str, str]]:
        """Get list of (symbol, side) for open positions."""
        cursor = conn.execute("""
            SELECT symbol, side FROM positions WHERE status = 'open' AND account_id = ?
        """, (account_id,))
        return cursor.fetchall()
    
        
    def get_portfolio_status(self, account_id: Optional[str] = None) -> Dict:
        """Get current portfolio status."""
        conn = self._get_conn()
        try:
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
            
            # Daily stats directly from positions
            today = date.today().isoformat()
            query = """
                SELECT COALESCE(SUM(pnl), 0), COUNT(*) 
                FROM positions 
                WHERE status = 'closed' AND DATE(COALESCE(exit_time, entry_time)) = ?
            """
            params = [today]
            if account_id and account_id != "all":
                query += " AND account_id = ?"
                params.append(account_id)
            cursor = conn.execute(query, tuple(params))
            
            row = cursor.fetchone()
            daily_stats = {
                "date": today,
                "total_pnl": round(row[0], 2) if row and row[0] is not None else 0,
                "trades_count": row[1] if row and row[1] is not None else 0,
                "loss_streak": 0
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
        finally:
            conn.close()
    
    def get_position_count(self, symbol: Optional[str] = None, account_id: Optional[str] = None) -> int:
        """Get count of open positions, optionally filtered by symbol."""
        conn = self._get_conn()
        try:
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
            return count
        except Exception as e:
            logger.error(f"Failed to get position count: {e}")
            return 0
        finally:
            conn.close()
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

    def update_cbot_config(self, name: str, description: str, run_command: str) -> bool:
        """Update existing bot configuration."""
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE cbot_configs SET description = ?, run_command = ? WHERE name = ?",
                (description, run_command, name)
            )
            conn.commit()
            return cursor.rowcount > 0
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

    def update_market_price(self, symbol: str, bid: float, ask: float, bot_id: Optional[str] = None, position_data: Optional[Dict] = None, account_id: Optional[str] = None):
        """Track latest market prices and position metrics."""
        if not hasattr(self, "_latest_prices"):
            self._latest_prices = {}
        if not hasattr(self, "_bot_positions_cache"):
            self._bot_positions_cache = {}
        
        self._latest_prices[symbol] = {
            "bid": bid,
            "ask": ask,
            "time": datetime.now().isoformat()
        }
        if bot_id and position_data:
            self._bot_positions_cache[bot_id] = position_data
            if account_id:
                self._bot_positions_cache[f"{account_id}:{bot_id}"] = position_data

    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        """Get cached latest price for symbol."""
        if not hasattr(self, "_latest_prices"):
            self._latest_prices = {}
        return self._latest_prices.get(symbol)

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
