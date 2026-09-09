#!/usr/bin/env python3
"""
Migration script from SQLite portfolio.db to PostgreSQL agentfx.
Preserves all historical accounts, positions, cbot_configs, and news_assessments.
"""

import sqlite3
import psycopg2
import psycopg2.extras
import sys
from pathlib import Path

PG_DSN = "postgresql://agentfx:kaz%40112358.@127.0.0.1:5432/agentfx"
SQLITE_PATH = Path(__file__).resolve().parent.parent / "portfolio.db"


def migrate():
    if not SQLITE_PATH.exists():
        print(f"SQLite database not found at {SQLITE_PATH}")
        sys.exit(1)

    print(f"Connecting to SQLite: {SQLITE_PATH}")
    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    print("Connecting to PostgreSQL...")
    pg_conn = psycopg2.connect(PG_DSN)
    pg_cur = pg_conn.cursor()

    # 1. Create tables in PostgreSQL
    print("Creating tables in PostgreSQL...")
    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id VARCHAR(64) PRIMARY KEY,
            account_number VARCHAR(64) NOT NULL,
            account_type VARCHAR(16) NOT NULL CHECK(account_type IN ('live','demo')),
            label VARCHAR(128) NOT NULL,
            last_balance DOUBLE PRECISION DEFAULT 0,
            last_equity DOUBLE PRECISION DEFAULT 0,
            last_seen TEXT,
            is_configured INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_number_type ON accounts(account_number, account_type);

        CREATE TABLE IF NOT EXISTS positions (
            id SERIAL PRIMARY KEY,
            bot_id VARCHAR(64) NOT NULL,
            symbol VARCHAR(32) NOT NULL,
            side VARCHAR(16) NOT NULL,
            volume DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            sl_pips DOUBLE PRECISION,
            tp_pips DOUBLE PRECISION,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            exit_price DOUBLE PRECISION,
            pnl DOUBLE PRECISION,
            status VARCHAR(16) DEFAULT 'open',
            account_id VARCHAR(64) NOT NULL DEFAULT 'default',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
        CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
        CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id);
        CREATE INDEX IF NOT EXISTS idx_positions_account_id ON positions(account_id);
        CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_id, status);
        CREATE INDEX IF NOT EXISTS idx_positions_exit_time ON positions(exit_time);
        CREATE INDEX IF NOT EXISTS idx_positions_entry_time ON positions(entry_time);

        CREATE TABLE IF NOT EXISTS cbot_configs (
            id SERIAL PRIMARY KEY,
            name VARCHAR(128) UNIQUE NOT NULL,
            description TEXT,
            run_command TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS news_assessments (
            id SERIAL PRIMARY KEY,
            cluster_id VARCHAR(64) NOT NULL,
            timestamp_utc TEXT NOT NULL,
            symbol VARCHAR(32) NOT NULL,
            volatility_level VARCHAR(32),
            expected_pips_range VARCHAR(32),
            trend_type VARCHAR(32),
            prob_buy DOUBLE PRECISION,
            prob_sell DOUBLE PRECISION,
            scenario_better_vi TEXT,
            scenario_better_en TEXT,
            scenario_worse_vi TEXT,
            scenario_worse_en TEXT,
            bot_guidance_vi TEXT,
            bot_guidance_en TEXT,
            analysis_markdown_vi TEXT,
            analysis_markdown_en TEXT,
            events_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_news_cluster ON news_assessments (cluster_id, symbol);

        CREATE OR REPLACE VIEW daily_stats AS
        SELECT 
            account_id,
            bot_id,
            DATE(COALESCE(exit_time, entry_time)::timestamp) as date,
            ROUND(SUM(pnl)::numeric, 2) as total_pnl,
            COUNT(*) as trades_count,
            0 as loss_streak,
            MAX(COALESCE(exit_time, entry_time)) as updated_at
        FROM positions
        WHERE status = 'closed'
        GROUP BY account_id, bot_id, DATE(COALESCE(exit_time, entry_time)::timestamp);
    """)
    pg_conn.commit()

    # 2. Migrate accounts
    print("Migrating accounts...")
    s_cur = sqlite_conn.cursor()
    s_cur.execute("SELECT * FROM accounts")
    acc_rows = s_cur.fetchall()
    for row in acc_rows:
        pg_cur.execute("""
            INSERT INTO accounts (account_id, account_number, account_type, label, last_balance, last_equity, last_seen, is_configured, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id) DO UPDATE SET
                account_number = EXCLUDED.account_number,
                account_type = EXCLUDED.account_type,
                label = EXCLUDED.label,
                last_balance = EXCLUDED.last_balance,
                last_equity = EXCLUDED.last_equity,
                last_seen = EXCLUDED.last_seen,
                is_configured = EXCLUDED.is_configured;
        """, (row["account_id"], row["account_number"], row["account_type"], row["label"],
              row["last_balance"], row["last_equity"], row["last_seen"], row["is_configured"], row["created_at"]))
    pg_conn.commit()
    print(f"  Migrated {len(acc_rows)} accounts.")

    # 3. Migrate cbot_configs
    print("Migrating cbot_configs...")
    s_cur.execute("SELECT * FROM cbot_configs")
    cfg_rows = s_cur.fetchall()
    for row in cfg_rows:
        pg_cur.execute("""
            INSERT INTO cbot_configs (id, name, description, run_command, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                run_command = EXCLUDED.run_command;
        """, (row["id"], row["name"], row["description"], row["run_command"], row["created_at"]))
    if cfg_rows:
        pg_cur.execute("SELECT setval('cbot_configs_id_seq', (SELECT COALESCE(MAX(id), 1) FROM cbot_configs));")
    pg_conn.commit()
    print(f"  Migrated {len(cfg_rows)} cbot_configs.")

    # 4. Migrate positions
    print("Migrating positions...")
    s_cur.execute("SELECT * FROM positions ORDER BY id ASC")
    pos_rows = s_cur.fetchall()
    for row in pos_rows:
        pg_cur.execute("""
            INSERT INTO positions (id, bot_id, symbol, side, volume, entry_price, sl_pips, tp_pips, entry_time, exit_time, exit_price, pnl, status, account_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                exit_time = EXCLUDED.exit_time,
                exit_price = EXCLUDED.exit_price,
                pnl = EXCLUDED.pnl;
        """, (row["id"], row["bot_id"], row["symbol"], row["side"], row["volume"],
              row["entry_price"], row["sl_pips"], row["tp_pips"], row["entry_time"],
              row["exit_time"], row["exit_price"], row["pnl"], row["status"],
              row["account_id"], row["created_at"]))
    if pos_rows:
        pg_cur.execute("SELECT setval('positions_id_seq', (SELECT COALESCE(MAX(id), 1) FROM positions));")
    pg_conn.commit()
    print(f"  Migrated {len(pos_rows)} positions.")

    # 5. Migrate news_assessments if any
    try:
        s_cur.execute("SELECT * FROM news_assessments ORDER BY id ASC")
        news_rows = s_cur.fetchall()
        for row in news_rows:
            pg_cur.execute("""
                INSERT INTO news_assessments (id, cluster_id, timestamp_utc, symbol, volatility_level, expected_pips_range, trend_type, prob_buy, prob_sell, scenario_better_vi, scenario_better_en, scenario_worse_vi, scenario_worse_en, bot_guidance_vi, bot_guidance_en, analysis_markdown_vi, analysis_markdown_en, events_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING;
            """, (row["id"], row["cluster_id"], row["timestamp_utc"], row["symbol"],
                  row["volatility_level"], row["expected_pips_range"], row["trend_type"],
                  row["prob_buy"], row["prob_sell"], row["scenario_better_vi"],
                  row["scenario_better_en"], row["scenario_worse_vi"], row["scenario_worse_en"],
                  row["bot_guidance_vi"], row["bot_guidance_en"], row["analysis_markdown_vi"],
                  row["analysis_markdown_en"], row["events_json"], row["created_at"]))
        if news_rows:
            pg_cur.execute("SELECT setval('news_assessments_id_seq', (SELECT COALESCE(MAX(id), 1) FROM news_assessments));")
        pg_conn.commit()
        print(f"  Migrated {len(news_rows)} news_assessments.")
    except Exception as e:
        print(f"  No news_assessments to migrate or error: {e}")

    # Verify counts
    pg_cur.execute("SELECT count(*) FROM accounts")
    print(f"PostgreSQL accounts count: {pg_cur.fetchone()[0]}")
    pg_cur.execute("SELECT count(*) FROM positions")
    print(f"PostgreSQL positions count: {pg_cur.fetchone()[0]}")
    pg_cur.execute("SELECT count(*) FROM cbot_configs")
    print(f"PostgreSQL cbot_configs count: {pg_cur.fetchone()[0]}")

    sqlite_conn.close()
    pg_conn.close()
    print("Migration completed successfully!")


if __name__ == "__main__":
    migrate()
