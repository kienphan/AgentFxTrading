import os
import sqlite3
import logging
from contextlib import contextmanager
from typing import List, Dict, Optional, Generator
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
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()
        
    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
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
            conn.commit()
            
    def seed_from_env(self):
        accounts = parse_dashboard_accounts_env()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for acc in accounts:
                cursor.execute("""
                INSERT INTO accounts (account_id, account_number, account_type, label, is_configured)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(account_number, account_type) DO UPDATE SET
                    account_id = excluded.account_id,
                    label = excluded.label,
                    is_configured = 1
                """, (acc["account_id"], acc["account_number"], acc["account_type"], acc["label"]))
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
                update_query = """
                UPDATE accounts 
                SET last_balance = ?, last_equity = ?, last_seen = datetime('now')
                """
                params = [balance, equity]
                
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

# Global registry accessor pattern
_account_registry = None

def init_account_registry(db_path: str) -> AccountRegistry:
    global _account_registry
    _account_registry = AccountRegistry(db_path)
    return _account_registry

def get_account_registry() -> AccountRegistry:
    if _account_registry is None:
        raise RuntimeError("AccountRegistry not initialized")
    return _account_registry