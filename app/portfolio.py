"""
Portfolio Manager with SQLite backend.
Tracks positions across multiple bots and enforces portfolio-level risk limits.
"""

import logging
from datetime import datetime, date, timezone
from typing import Dict, List, Tuple, Optional
from pathlib import Path
from app.accounts import get_account_registry
from app.db import get_db_connection

logger = logging.getLogger(__name__)


class PortfolioConfig:
    """Portfolio risk limits."""
    MAX_DAILY_LOSS = -200.0  # USD
    MAX_MARGIN_USAGE_PCT = 50.0  # % of account
    
US_INDEX_SYMBOLS = {"US30", "DJ30", "USTEC", "NAS100", "US500", "SPX500"}


def is_us_index(symbol: Optional[str]) -> bool:
    """Check if a symbol is a US Equity Index."""
    if not symbol:
        return False
    sym_up = symbol.upper()
    return any(tok in sym_up for tok in US_INDEX_SYMBOLS)



class PortfolioManager:
    """Manages portfolio-level risk across multiple trading bots."""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else None
        self.config = PortfolioConfig()
        self._init_db()
    
    def _init_db(self):
        """Initialize database with schema."""
        conn = self._get_conn()
        try:
            # Setup accounts table
            registry = get_account_registry()
            registry._init_schema()

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
        
            # Create automatic dynamic view for daily_stats
            try:
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
            except Exception as e:
                logger.debug(f"daily_stats view init: {e}")

            # Create indexes for performance
            # The cBot has always sent ctrader_id, but the original schema had nowhere to put
            # it, so every close matched on (bot_id, symbol) alone. Harmless while
            # MaxPositionsAllowed is 1; the moment it is raised, one close report would close
            # every open position for that pair. Additive, nullable, safe to re-run.
            try:
                conn.execute("ALTER TABLE positions ADD COLUMN ctrader_id BIGINT")
            except Exception:
                pass  # already present

            # SL/TP as prices (the pip distances above are fixed at entry and cannot show a
            # trailed stop) plus why the position closed, for the dashboard. On close the prices
            # are overwritten with the levels the broker held at that moment. DOUBLE PRECISION,
            # not REAL: on PostgreSQL REAL is float4 and would round a BTC price.
            for col_sql in [
                "ALTER TABLE positions ADD COLUMN sl_price DOUBLE PRECISION",
                "ALTER TABLE positions ADD COLUMN tp_price DOUBLE PRECISION",
                "ALTER TABLE positions ADD COLUMN close_reason TEXT",
                # The volume the position opened with. record_partial_close shrinks `volume` to
                # what is still open, which the margin check needs, but a closed row carries the
                # P&L of every slice, so on close `volume` is restored from this.
                "ALTER TABLE positions ADD COLUMN initial_volume DOUBLE PRECISION",
            ]:
                try:
                    conn.execute(col_sql)
                except Exception:
                    pass  # already present

            # The original columns were declared REAL, which PostgreSQL stores as float4: seven
            # significant digits, so a BTC price above 100000 loses its cents. Widen them once.
            # Cast through text: float4 -> numeric keeps only six digits (86759.45 -> 86759.4),
            # while float4 -> text is the shortest exact form. `pnl` stays: the daily_stats view
            # depends on it and PostgreSQL refuses to retype a column a view reads. On SQLite
            # REAL is already a double and information_schema does not exist.
            try:
                cur = conn.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'positions' AND data_type = 'real'
                      AND column_name IN ('volume', 'entry_price', 'exit_price', 'sl_pips', 'tp_pips')
                """)
                float4_cols = [r[0] for r in cur.fetchall()]
            except Exception:
                float4_cols = []
            for col in float4_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE positions ALTER COLUMN {col} TYPE DOUBLE PRECISION "
                        f"USING {col}::text::double precision"
                    )
                    conn.commit()
                    logger.info(f"positions.{col} widened from float4 to double precision")
                except Exception as e:
                    logger.warning(f"Could not widen positions.{col}: {e}")

            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)",
                "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)",
                "CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id)",
                "CREATE INDEX IF NOT EXISTS idx_positions_account_id ON positions(account_id)",
                "CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_positions_exit_time ON positions(exit_time)",
                "CREATE INDEX IF NOT EXISTS idx_positions_entry_time ON positions(entry_time)",
                "CREATE INDEX IF NOT EXISTS idx_positions_ctrader_id ON positions(ctrader_id)",
            ]:
                try:
                    conn.execute(idx_sql)
                except Exception:
                    pass

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
            logger.info(f"Portfolio database initialized (target: {self.db_path or 'PostgreSQL'})")
        finally:
            conn.close()

    def _get_conn(self):
        """Get database connection."""
        return get_db_connection(self.db_path)

    def register_position(self, bot_id: str, symbol: str, side: str, 
                         volume: float, entry_price: float, 
                         sl_pips: float, tp_pips: float, account_id: str,
                         ctrader_id: Optional[int] = None,
                         sl_price: Optional[float] = None, tp_price: Optional[float] = None) -> bool:
        """Register new position after trade execution."""
        conn = self._get_conn()
        try:
            conn.execute("""
                INSERT INTO positions (bot_id, symbol, side, volume, entry_price,
                                     sl_pips, tp_pips, entry_time, status, account_id, ctrader_id,
                                     sl_price, tp_price, initial_volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), 'open', ?, ?, ?, ?, ?)
            """, (bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, account_id, ctrader_id,
                  sl_price, tp_price, volume))
            conn.commit()
            logger.info(f"Position registered: {symbol} {side} {volume} lots by {bot_id} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to register position: {e}")
            return False
        finally:
            conn.close()
    def get_open_positions(self, bot_id: str, account_id: str) -> List[Dict]:
        """
        Open positions with the stop distance recorded at entry.

        A cBot keeps initial SL distances in RAM, so a restart loses them and it would
        otherwise re-derive R from whatever stop the position carries now - which for a
        position already moved to break-even is near zero, inflating R enormously.
        """
        conn = self._get_conn()
        try:
            cur = conn.execute("""
                SELECT symbol, side, volume, entry_price, sl_pips, tp_pips, entry_time
                FROM positions
                WHERE bot_id = ? AND account_id = ? AND status = 'open'
            """, (bot_id, account_id))
            return [
                {
                    "symbol": r[0],
                    "side": r[1],
                    "volume": r[2],
                    "entry_price": r[3],
                    "sl_pips": r[4],
                    "tp_pips": r[5],
                    "entry_time": r[6],
                }
                for r in cur.fetchall()
            ]
        except Exception as e:
            logger.error(f"Failed to read open positions: {e}")
            return []
        finally:
            conn.close()

    def record_partial_close(self, bot_id: str, symbol: str, remaining_volume: float,
                             realized_pnl: float, account_id: str,
                             ctrader_id: Optional[int] = None) -> bool:
        """
        Bank a partial close against the still-open position.

        cTrader's Positions.Closed event does not fire on a partial close, so without
        this the profit taken at break-even was never recorded: the row kept its
        original volume and the final close reported only the remainder's P&L.

        No schema change is needed. `pnl` is unused (NULL) while a position is open and
        the daily_stats view reads only `status = 'closed'`, so the open row can carry
        realised partial P&L until close_position adds the remainder to it.
        """
        conn = self._get_conn()
        try:
            matched = 0
            if ctrader_id is not None:
                # 1. Primary match: exact ctrader_id
                sql = """
                    UPDATE positions
                    SET initial_volume = COALESCE(initial_volume, volume),
                        volume = ?, pnl = COALESCE(pnl, 0) + ?
                    WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ? AND ctrader_id = ?
                """
                cur = conn.execute(sql, (remaining_volume, realized_pnl, bot_id, symbol, account_id, ctrader_id))
                matched = cur.rowcount

                if not matched:
                    # 2. Fallback: position registered without ctrader_id (e.g. older bot/payload),
                    # match open position where ctrader_id IS NULL and link ctrader_id
                    fallback_sql = """
                        UPDATE positions
                        SET initial_volume = COALESCE(initial_volume, volume),
                            volume = ?, pnl = COALESCE(pnl, 0) + ?, ctrader_id = ?
                        WHERE id = (
                            SELECT id FROM positions
                            WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                              AND ctrader_id IS NULL
                            ORDER BY entry_time ASC
                            LIMIT 1
                        )
                    """
                    cur = conn.execute(fallback_sql, (remaining_volume, realized_pnl, ctrader_id, bot_id, symbol, account_id))
                    matched = cur.rowcount

                if not matched:
                    # 3. Last resort: match any open position for this bot/symbol/account
                    last_resort_sql = """
                        UPDATE positions
                        SET initial_volume = COALESCE(initial_volume, volume),
                            volume = ?, pnl = COALESCE(pnl, 0) + ?, ctrader_id = COALESCE(?, ctrader_id)
                        WHERE id = (
                            SELECT id FROM positions
                            WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                            ORDER BY entry_time ASC
                            LIMIT 1
                        )
                    """
                    cur = conn.execute(last_resort_sql, (remaining_volume, realized_pnl, ctrader_id, bot_id, symbol, account_id))
                    matched = cur.rowcount
            else:
                # No ctrader_id supplied: update the oldest open position
                sql = """
                    UPDATE positions
                    SET initial_volume = COALESCE(initial_volume, volume),
                        volume = ?, pnl = COALESCE(pnl, 0) + ?
                    WHERE id = (
                        SELECT id FROM positions
                        WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                        ORDER BY entry_time ASC
                        LIMIT 1
                    )
                """
                cur = conn.execute(sql, (remaining_volume, realized_pnl, bot_id, symbol, account_id))
                matched = cur.rowcount

            conn.commit()
            if not matched:
                logger.warning(
                    f"Partial close ignored: no open position for {symbol} by {bot_id} "
                    f"(ctrader_id={ctrader_id}) on account {account_id}"
                )
                return False
            logger.info(
                f"Partial close recorded: {symbol} by {bot_id}, realised {realized_pnl}, "
                f"remaining {remaining_volume} lots for account {account_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to record partial close: {e}")
            return False
        finally:
            conn.close()

    def close_position(self, bot_id: str, symbol: str, exit_price: float, pnl: float,
                       account_id: str, ctrader_id: Optional[int] = None,
                       close_reason: Optional[str] = None,
                       sl_price: Optional[float] = None, tp_price: Optional[float] = None) -> bool:
        """Mark position as closed (single source of truth)."""
        conn = self._get_conn()
        try:
            matched = 0
            if ctrader_id is not None:
                # 1. Primary match: exact ctrader_id
                sql = """
                    UPDATE positions 
                    SET status = 'closed', exit_price = ?, pnl = COALESCE(pnl, 0) + ?, exit_time = datetime('now'),
                        volume = COALESCE(initial_volume, volume),
                        close_reason = ?, sl_price = COALESCE(?, sl_price), tp_price = COALESCE(?, tp_price)
                    WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ? AND ctrader_id = ?
                """
                cur = conn.execute(sql, (exit_price, pnl, close_reason, sl_price, tp_price, bot_id, symbol, account_id, ctrader_id))
                matched = cur.rowcount

                if not matched:
                    # 2. Fallback: position registered without ctrader_id, link ctrader_id upon closing
                    fallback_sql = """
                        UPDATE positions 
                        SET status = 'closed', exit_price = ?, pnl = COALESCE(pnl, 0) + ?, exit_time = datetime('now'),
                            volume = COALESCE(initial_volume, volume),
                            close_reason = ?, sl_price = COALESCE(?, sl_price), tp_price = COALESCE(?, tp_price),
                            ctrader_id = ?
                        WHERE id = (
                            SELECT id FROM positions
                            WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                              AND ctrader_id IS NULL
                            ORDER BY entry_time ASC
                            LIMIT 1
                        )
                    """
                    cur = conn.execute(fallback_sql, (exit_price, pnl, close_reason, sl_price, tp_price, ctrader_id, bot_id, symbol, account_id))
                    matched = cur.rowcount

                if not matched:
                    # 3. Last resort: match any open position for this bot/symbol/account
                    last_resort_sql = """
                        UPDATE positions 
                        SET status = 'closed', exit_price = ?, pnl = COALESCE(pnl, 0) + ?, exit_time = datetime('now'),
                            volume = COALESCE(initial_volume, volume),
                            close_reason = ?, sl_price = COALESCE(?, sl_price), tp_price = COALESCE(?, tp_price),
                            ctrader_id = COALESCE(?, ctrader_id)
                        WHERE id = (
                            SELECT id FROM positions
                            WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                            ORDER BY entry_time ASC
                            LIMIT 1
                        )
                    """
                    cur = conn.execute(last_resort_sql, (exit_price, pnl, close_reason, sl_price, tp_price, ctrader_id, bot_id, symbol, account_id))
                    matched = cur.rowcount
            else:
                # No ctrader_id supplied: update open position (for legacy single-position bots)
                sql = """
                    UPDATE positions 
                    SET status = 'closed', exit_price = ?, pnl = COALESCE(pnl, 0) + ?, exit_time = datetime('now'),
                        volume = COALESCE(initial_volume, volume),
                        close_reason = ?, sl_price = COALESCE(?, sl_price), tp_price = COALESCE(?, tp_price)
                    WHERE bot_id = ? AND symbol = ? AND status = 'open' AND account_id = ?
                """
                cur = conn.execute(sql, (exit_price, pnl, close_reason, sl_price, tp_price, bot_id, symbol, account_id))
                matched = cur.rowcount

            conn.commit()
            if not matched:
                logger.warning(
                    f"Close position ignored: no open position for {symbol} by {bot_id} "
                    f"(ctrader_id={ctrader_id}) on account {account_id}"
                )
                return False

            # Clear cache for this bot so stale live metrics do not linger
            if hasattr(self, "_bot_positions_cache"):
                self._bot_positions_cache.pop(bot_id, None)
                if account_id:
                    self._bot_positions_cache.pop(f"{account_id}:{bot_id}", None)
                for key in list(self._bot_positions_cache.keys()):
                    if key.endswith(f":{bot_id}"):
                        self._bot_positions_cache.pop(key, None)

            logger.info(f"Position closed: {symbol} by {bot_id}, PnL: {pnl} for account {account_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to close position: {e}")
            return False
        finally:
            conn.close()
    def check_risk(self, symbol: str, side: str, volume: float,
                   account_balance: float = 10000.0, account_id: str = "default",
                   used_margin: Optional[float] = None) -> Tuple[bool, str]:
        """
        Check if new trade is safe at portfolio level.
        Returns (allowed: bool, reason: str)

        used_margin is the broker's own figure (cTrader Account.Margin) when the cBot sends it.
        """
        conn = self._get_conn()
        try:
            # 1. US Index Correlation / Directional Alignment Check
            # All US Equity Indices (US30, USTEC, US500) must align with macro trend direction.
            # Blocking opposing positions across US indices prevents portfolio self-hedging and divergence losses.
            if is_us_index(symbol) and side.upper() in ("BUY", "SELL"):
                cursor = conn.execute(
                    "SELECT symbol, side FROM positions WHERE status = 'open' AND account_id = ?",
                    (account_id,)
                )
                open_positions = cursor.fetchall()
                for row in open_positions:
                    pos_sym = row[0]
                    pos_side = str(row[1]).upper()
                    if is_us_index(pos_sym):
                        if pos_side != side.upper():
                            return False, f"US Index alignment conflict: cannot open {side.upper()} {symbol} while {pos_sym} has open {pos_side} position"

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
            # 5. Margin usage. Prefer the margin the broker reports. The fallback prices every
            # lot at $1000 whatever the instrument, so 0.4 lots of ETHUSD and 0.3 of DE40 counted
            # like forex lots and blocked US30/USTEC/XAUUSD entries on 2026-09-23 at a "64.4%"
            # the account never used.
            if used_margin is not None and used_margin >= 0:
                margin_pct = (used_margin / account_balance) * 100 if account_balance > 0 else 0.0
            else:
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

    def count_positions_opened_on(self, bot_id: str, symbol: str, side: str, account_id: str,
                                  day: Optional[date] = None) -> int:
        """Count positions (open or closed) opened on a given UTC date for one bot/symbol/side.

        Used by the Judas sweep dedupe gate: allow at most one sweep trade per
        Asian boundary per session (one BUY at the Asian Low, one SELL at the Asian High).
        """
        conn = self._get_conn()
        try:
            day = day or datetime.now(timezone.utc).date()
            cursor = conn.execute(
                "SELECT COUNT(*) FROM positions "
                "WHERE bot_id = ? AND account_id = ? AND symbol = ? AND UPPER(side) = ? AND DATE(entry_time) = ?",
                (bot_id, account_id, symbol, side.upper(), day.isoformat()),
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.error(f"count_positions_opened_on failed: {e}")
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
            "time": datetime.now().isoformat(),
            "ts": datetime.now(timezone.utc).timestamp()
        }
        if bot_id:
            if position_data:
                # Stamp the report: price and P&L only arrive with a bot snapshot, so consumers
                # need the age to avoid presenting a stale figure as a live one.
                reported = dict(position_data, _reported_at=datetime.now(timezone.utc).timestamp())
                # The P&L-at-SL/TP estimate rides on ticks, not snapshots. Carry it over while the
                # level it was computed for has not moved, so each bar close does not blank it.
                prev = self._bot_positions_cache.get(f"{account_id}:{bot_id}" if account_id else bot_id) or {}
                for price_key, pnl_key in (("sl_price", "sl_pnl"), ("tp_price", "tp_pnl")):
                    if pnl_key not in reported and prev.get(price_key) == reported.get(price_key):
                        reported[pnl_key] = prev.get(pnl_key)
                self._bot_positions_cache[bot_id] = reported
                if account_id:
                    self._bot_positions_cache[f"{account_id}:{bot_id}"] = reported
            else:
                self._bot_positions_cache.pop(bot_id, None)
                if account_id:
                    self._bot_positions_cache.pop(f"{account_id}:{bot_id}", None)
                for key in list(self._bot_positions_cache.keys()):
                    if key.endswith(f":{bot_id}"):
                        self._bot_positions_cache.pop(key, None)

    def update_position_metrics(self, bot_id: str, unrealized_pnl: float, unrealized_pnl_pips: float,
                                account_id: Optional[str] = None,
                                levels: Optional[Dict] = None) -> None:
        """Merge a live P&L sample from a bot tick into the cached position report.

        The bot owns these numbers (net profit in the account currency, pips from cTrader's own
        pip size), so a tick refreshes them between bar snapshots without the server recomputing
        anything. Creates the entry when a tick arrives before the next snapshot, e.g. right
        after a restart while the position row already exists.
        """
        if not hasattr(self, "_bot_positions_cache"):
            self._bot_positions_cache = {}

        reported_at = datetime.now(timezone.utc).timestamp()

        # Refresh every key that belongs to this bot. A /trade snapshot writes
        # "<account_id>:<bot_id>" and the dashboard resolves that key before the bare bot_id
        # one, so a tick that only touched the bare key would stay hidden behind the older
        # snapshot value. The tick itself carries no account id (the bot cannot derive it), so
        # the keys are taken from whatever the snapshots already registered.
        keys = {bot_id}
        if account_id:
            keys.add(f"{account_id}:{bot_id}")
        keys.update(key for key in self._bot_positions_cache if key.endswith(f":{bot_id}"))

        for key in keys:
            entry = dict(self._bot_positions_cache.get(key) or {})
            entry["unrealized_pnl"] = unrealized_pnl
            entry["unrealized_pnl_pips"] = unrealized_pnl_pips
            # SL/TP prices and the bot's P&L estimate at each (sl_price, tp_price, sl_pnl,
            # tp_pnl). None means "no stop / no target", so it replaces the old value; a tick
            # from a bot that does not send levels at all passes levels=None and keeps whatever
            # its last snapshot reported.
            if levels is not None:
                entry.update(levels)
            entry["_reported_at"] = reported_at
            self._bot_positions_cache[key] = entry

    def get_latest_price(self, symbol: str) -> Optional[Dict]:
        """Get cached latest price for symbol."""
        if not hasattr(self, "_latest_prices"):
            self._latest_prices = {}
        return self._latest_prices.get(symbol)

# Global instance (will be initialized in server.py)
portfolio_manager: Optional[PortfolioManager] = None


def init_portfolio(db_path: Optional[str] = None) -> PortfolioManager:
    """Initialize global portfolio manager instance."""
    global portfolio_manager
    portfolio_manager = PortfolioManager(db_path)
    return portfolio_manager


def get_portfolio_manager() -> PortfolioManager:
    """Get global portfolio manager instance."""
    if portfolio_manager is None:
        raise RuntimeError("Portfolio manager not initialized. Call init_portfolio() first.")
    return portfolio_manager
