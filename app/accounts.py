import os
import logging
from contextlib import contextmanager
from typing import List, Dict, Optional, Generator, Any
from app.db import get_db_connection
logger = logging.getLogger(__name__)

def parse_dashboard_accounts_env() -> List[Dict]:
    """
    Read DASHBOARD_ACCOUNTS from env.
    Format (semicolon-separated entries): account_id|account_number|type|label
    Example: live-main|1234567|live|Live Main;demo-1|7654321|demo|Demo Test
    """
    env_str = os.getenv("DASHBOARD_ACCOUNTS", "")
    if not env_str:
        return []
        
    accounts = []
    for entry in env_str.split(";"):
        if not entry.strip():
            continue
            
        parts = entry.split("|")
        if len(parts) != 4:
            logger.warning(f"Malformed account entry in DASHBOARD_ACCOUNTS: {entry}")
            continue
            
        account_id, account_number, acc_type, label = parts
        acc_type = acc_type.lower()
        if acc_type not in ("live", "demo"):
            logger.warning(f"Invalid account type '{acc_type}' in DASHBOARD_ACCOUNTS entry: {entry}. Must be live or demo.")
            continue
            
        accounts.append({
            "account_id": account_id,
            "account_number": account_number,
            "account_type": acc_type,
            "label": label
        })
        
    return accounts

class AccountRegistry:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self._init_schema()
        
    @contextmanager
    def _get_connection(self) -> Generator[Any, None, None]:
        conn = get_db_connection(self.db_path)
        try:
            yield conn
        finally:
            conn.close()
    def _init_schema(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_id TEXT PRIMARY KEY,
                account_number TEXT NOT NULL,
                account_type TEXT NOT NULL CHECK(account_type IN ('live','demo')),
                label TEXT NOT NULL,
                last_balance REAL DEFAULT 0,
                last_equity REAL DEFAULT 0,
                last_seen TEXT,
                is_configured INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_number_type ON accounts(account_number, account_type);")
            # cTrader login accounts used by the dashboard's "Setup Instances" screen.
            # pwd_file is the in-container path; the password itself lives only on disk.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS ctrader_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                ctid_email TEXT NOT NULL,
                account_number TEXT NOT NULL,
                account_type TEXT NOT NULL CHECK(account_type IN ('live','demo')),
                label TEXT NOT NULL,
                pwd_file TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            conn.commit()
            
    def seed_from_env(self):
        for acc in parse_dashboard_accounts_env():
            self.upsert_configured_account(acc["account_id"], acc["account_number"], acc["account_type"], acc["label"])

    def upsert_configured_account(self, account_id: str, account_number: str, account_type: str, label: str) -> None:
        """Insert or relabel a dashboard account and mark it configured (unique on number+type)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO accounts (account_id, account_number, account_type, label, is_configured)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(account_number, account_type) DO UPDATE SET
                account_id = excluded.account_id,
                label = excluded.label,
                is_configured = 1
            """, (account_id, account_number, account_type, label))
            conn.commit()
            
    def upsert_from_bot(self, account_number: str, account_type: str, label: Optional[str], balance: float, equity: float) -> str:
        acc_type = account_type.lower() if account_type else "demo"
        if acc_type not in ("live", "demo"):
            acc_type = "demo"
            
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. Exact match (account_number, acc_type)
            cursor.execute("SELECT account_id FROM accounts WHERE account_number = ? AND account_type = ?", (account_number, acc_type))
            row = cursor.fetchone()
            
            # 2. If no exact match, but this account_number is already configured, prioritize the configured account!
            if not row:
                cursor.execute("SELECT account_id FROM accounts WHERE account_number = ? AND is_configured = 1", (account_number,))
                row = cursor.fetchone()
            if row:
                account_id = row["account_id"]
                # Only overwrite last_balance and last_equity if balance > 0.
                # Disconnected or unauthenticated cBots report 0.0, which must never wipe a valid balance.
                if balance > 0:
                    update_query = """
                    UPDATE accounts 
                    SET last_balance = ?, last_equity = ?, last_seen = datetime('now')
                    """
                    params = [balance, equity]
                else:
                    update_query = """
                    UPDATE accounts 
                    SET last_seen = datetime('now')
                    """
                    params = []
                
                if label is not None:
                    update_query += ", label = ?"
                    params.append(label)
                    
                update_query += " WHERE account_id = ?"
                params.append(account_id)
                
                cursor.execute(update_query, tuple(params))
            else:
                account_id = f"{acc_type}-{account_number}"
                final_label = label or f"{acc_type.upper()} {account_number}"
                
                cursor.execute("""
                INSERT INTO accounts (account_id, account_number, account_type, label, last_balance, last_equity, last_seen, is_configured)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), 0)
                """, (account_id, account_number, acc_type, final_label, balance, equity))
                
            conn.commit()
            return account_id
            
    def list_accounts(self, include_unconfigured: bool = False) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
            SELECT account_id, account_number, account_type, label, last_balance, last_equity, last_seen, is_configured
            FROM accounts
            """
            if not include_unconfigured:
                query += " WHERE is_configured = 1"
                
            query += " ORDER BY is_configured DESC, CASE WHEN account_type = 'live' THEN 0 ELSE 1 END, label ASC"
            
            cursor.execute(query)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
            
    def resolve_account_id(self, account_number: str, account_type: str) -> Optional[str]:
        acc_type = account_type.lower() if account_type else "demo"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT account_id FROM accounts WHERE account_number = ? AND account_type = ?", (account_number, acc_type))
            row = cursor.fetchone()
            return row["account_id"] if row else None

    def get_account_type(self, account_number: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT account_type FROM accounts WHERE account_number = ? ORDER BY is_configured DESC LIMIT 1", (account_number,))
            row = cursor.fetchone()
            return row["account_type"] if row else None

    # --- cTrader accounts (Setup Instances screen) ---

    _CTRADER_COLUMNS = "id, slug, ctid_email, account_number, account_type, label, pwd_file, created_at"

    def list_ctrader_accounts(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
            SELECT {self._CTRADER_COLUMNS} FROM ctrader_accounts
            ORDER BY CASE WHEN account_type = 'live' THEN 0 ELSE 1 END, label ASC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_ctrader_account(self, account_id: int) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {self._CTRADER_COLUMNS} FROM ctrader_accounts WHERE id = ?", (account_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_ctrader_account_by_slug(self, slug: str) -> Optional[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT {self._CTRADER_COLUMNS} FROM ctrader_accounts WHERE slug = ?", (slug,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def insert_ctrader_account(self, slug: str, ctid_email: str, account_number: str, account_type: str,
                               label: str, pwd_file: str) -> Dict:
        """Insert and return the new row. Raises the driver's IntegrityError on a duplicate slug."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO ctrader_accounts (slug, ctid_email, account_number, account_type, label, pwd_file)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (slug, ctid_email, account_number, account_type, label, pwd_file))
            conn.commit()
        # Re-read by slug: lastrowid is not available through the PostgreSQL wrapper.
        return self.get_ctrader_account_by_slug(slug)

    def delete_ctrader_account(self, account_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM ctrader_accounts WHERE id = ?", (account_id,))
            conn.commit()
            return cursor.rowcount > 0

# Global registry accessor pattern
_account_registry = None

def init_account_registry(db_path: Optional[str] = None) -> AccountRegistry:
    global _account_registry
    _account_registry = AccountRegistry(db_path)
    return _account_registry

def get_account_registry() -> AccountRegistry:
    if _account_registry is None:
        raise RuntimeError("AccountRegistry not initialized")
    return _account_registry