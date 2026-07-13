from __future__ import annotations

from typing import Any


MYSQL_SCHEMA_VERSION = 11


def ensure_mysql_schema(connection: Any) -> None:
    cursor = connection.cursor()
    statements = [
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            id INT PRIMARY KEY,
            version INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            userRole VARCHAR(32) NOT NULL DEFAULT 'user',
            username VARCHAR(64) NOT NULL,
            userAccount VARCHAR(128) NOT NULL UNIQUE,
            passwordHash VARCHAR(255) NOT NULL,
            bio VARCHAR(500) NOT NULL DEFAULT '',
            avatarUrl VARCHAR(512) NOT NULL DEFAULT '',
            cloudbaseUid VARCHAR(64) NULL,
            createdAt VARCHAR(64) NOT NULL,
            updatedAt VARCHAR(64) NOT NULL,
            isDeleted TINYINT NOT NULL DEFAULT 0,
            deletedAt VARCHAR(64) NULL,
            INDEX idx_users_cloudbase (cloudbaseUid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS reports (
            id VARCHAR(64) PRIMARY KEY,
            created_at VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            userId BIGINT NOT NULL,
            INDEX idx_reports_user (userId, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_profiles (
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            fund_name VARCHAR(255) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_state (
            userId BIGINT PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_daily_snapshots (
            userId BIGINT NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, snapshot_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_intraday_curves (
            userId BIGINT NOT NULL,
            trade_date VARCHAR(16) NOT NULL,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS investor_profile_state (
            userId BIGINT PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_prompt_state (
            userId BIGINT PRIMARY KEY,
            role_prompt LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_prompt_state (
            userId BIGINT PRIMARY KEY,
            role_prompt LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_transactions (
            id VARCHAR(64) PRIMARY KEY,
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NULL,
            fund_name VARCHAR(255) NOT NULL,
            direction VARCHAR(8) NOT NULL,
            amount_yuan DOUBLE NOT NULL,
            trade_time VARCHAR(32) NOT NULL,
            confirm_date VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL,
            shares_delta DOUBLE NULL,
            nav_on_confirm DOUBLE NULL,
            confirmed_shares DOUBLE NULL,
            fee_yuan DOUBLE NULL,
            shares_source VARCHAR(32) NULL,
            in_progress TINYINT NOT NULL DEFAULT 0,
            confirmed_at VARCHAR(64) NULL,
            dedup_key VARCHAR(255) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_fund_tx_dedup (userId, dedup_key),
            INDEX idx_fund_tx_fund (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS sector_mappings (
            userId BIGINT NOT NULL,
            sector_label VARCHAR(255) NOT NULL,
            source_type VARCHAR(64) NOT NULL,
            source_code VARCHAR(64) NULL,
            source_name VARCHAR(255) NOT NULL,
            confidence VARCHAR(32) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, sector_label)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_primary_sectors (
            userId BIGINT NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            sector_name VARCHAR(255) NOT NULL,
            intraday_index_name VARCHAR(255) NULL,
            source VARCHAR(64) NOT NULL,
            confidence DOUBLE NULL,
            detail LONGTEXT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_primary_sectors_global (
            fund_code VARCHAR(16) NOT NULL PRIMARY KEY,
            sector_name VARCHAR(255) NOT NULL,
            intraday_index_name VARCHAR(255) NULL,
            source VARCHAR(64) NOT NULL,
            confidence DOUBLE NULL,
            detail LONGTEXT NULL,
            resolved_at VARCHAR(64) NOT NULL,
            INDEX idx_fund_primary_sectors_global_resolved (resolved_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS ocr_text_cache (
            userId BIGINT NOT NULL,
            cache_key VARCHAR(255) NOT NULL,
            raw_text LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, cache_key)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS report_chat_messages (
            id VARCHAR(64) PRIMARY KEY,
            report_id VARCHAR(64) NOT NULL,
            role VARCHAR(32) NOT NULL,
            content LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            INDEX idx_chat_report (report_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(32) NOT NULL,
            request_payload LONGTEXT NOT NULL,
            report_id VARCHAR(64) NULL,
            error LONGTEXT NULL,
            stage VARCHAR(64) NULL,
            stage_label VARCHAR(255) NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_discovery_reports (
            id VARCHAR(64) PRIMARY KEY,
            created_at VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            INDEX idx_discovery_user_created (userId, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_jobs (
            id VARCHAR(64) PRIMARY KEY,
            status VARCHAR(32) NOT NULL,
            request_payload LONGTEXT NOT NULL,
            discovery_report_id VARCHAR(64) NULL,
            error LONGTEXT NULL,
            stage VARCHAR(64) NULL,
            stage_label VARCHAR(255) NULL,
            userId BIGINT NOT NULL DEFAULT 1,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS discovery_chat_messages (
            id VARCHAR(64) PRIMARY KEY,
            discovery_report_id VARCHAR(64) NOT NULL,
            role VARCHAR(32) NOT NULL,
            content LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            INDEX idx_discovery_chat_report (discovery_report_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS swing_alert_fired (
            userId BIGINT NOT NULL,
            trade_date VARCHAR(16) NOT NULL,
            alert_key VARCHAR(255) NOT NULL,
            payload LONGTEXT NOT NULL,
            fired_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, trade_date, alert_key),
            INDEX idx_swing_alert_user_date (userId, trade_date, fired_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id VARCHAR(64) PRIMARY KEY,
            userId BIGINT NOT NULL,
            tokenHash VARCHAR(255) NOT NULL,
            expiresAt VARCHAR(64) NOT NULL,
            createdAt VARCHAR(64) NOT NULL,
            revokedAt VARCHAR(64) NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS news_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS sector_spot_cache (
            cache_key VARCHAR(255) PRIMARY KEY,
            payload LONGTEXT NOT NULL,
            updated_at VARCHAR(64) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_snapshots (
            snapshot_id VARCHAR(64) PRIMARY KEY,
            schema_version INT NOT NULL,
            run_date VARCHAR(16) NOT NULL,
            generated_at VARCHAR(64) NOT NULL,
            published_at VARCHAR(64) NOT NULL,
            source_commit VARCHAR(64) NOT NULL,
            source_run_id VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            INDEX idx_factor_ic_generated (generated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_snapshots (
            snapshot_id VARCHAR(64) PRIMARY KEY,
            schema_version INT NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            available_at VARCHAR(64) NOT NULL,
            captured_at VARCHAR(64) NOT NULL,
            published_at VARCHAR(64) NOT NULL,
            source VARCHAR(64) NOT NULL,
            source_share_count INT NOT NULL,
            deduped_fund_count INT NOT NULL,
            sampled_fund_count INT NOT NULL,
            sample_target INT NOT NULL,
            fund_type_count INT NOT NULL,
            source_commit VARCHAR(64) NOT NULL,
            source_run_id VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            INDEX idx_factor_ic_universe_date (snapshot_date, available_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS factor_ic_universe_members (
            snapshot_id VARCHAR(64) NOT NULL,
            fund_code VARCHAR(16) NOT NULL,
            fund_name VARCHAR(255) NOT NULL,
            fund_type VARCHAR(32) NOT NULL,
            share_class VARCHAR(16) NULL,
            canonical_portfolio_key VARCHAR(64) NOT NULL,
            inception_date VARCHAR(16) NULL,
            available_at VARCHAR(64) NOT NULL,
            source_rank INT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (snapshot_id, fund_code),
            INDEX idx_factor_ic_universe_member_code (fund_code, snapshot_id),
            INDEX idx_factor_ic_universe_member_type (snapshot_id, fund_type),
            INDEX idx_factor_ic_universe_member_portfolio (canonical_portfolio_key, snapshot_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_portfolio_snapshots (
            userId BIGINT NOT NULL,
            snapshot_id VARCHAR(64) NOT NULL,
            account_id VARCHAR(128) NOT NULL DEFAULT 'default',
            snapshot_at VARCHAR(64) NOT NULL,
            snapshot_date VARCHAR(16) NOT NULL,
            source_type VARCHAR(64) NOT NULL,
            truth_status VARCHAR(32) NOT NULL,
            ledger_version VARCHAR(128) NULL,
            cash_yuan DOUBLE NULL,
            total_market_value_yuan DOUBLE NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, snapshot_id),
            INDEX idx_decision_snapshots_user_date (userId, snapshot_date, snapshot_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_events (
            userId BIGINT NOT NULL,
            event_id VARCHAR(255) NOT NULL,
            schema_version VARCHAR(64) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            source_type VARCHAR(32) NOT NULL,
            source_report_id VARCHAR(64) NULL,
            decision_at VARCHAR(64) NOT NULL,
            decision_date VARCHAR(16) NOT NULL,
            fund_code VARCHAR(32) NULL,
            fund_name VARCHAR(255) NULL,
            proposed_action VARCHAR(255) NULL,
            final_action VARCHAR(255) NOT NULL,
            action_category VARCHAR(32) NOT NULL,
            eligible TINYINT NOT NULL DEFAULT 0,
            amount_yuan DOUBLE NULL,
            portfolio_snapshot_id VARCHAR(64) NULL,
            benchmark_mapping_id VARCHAR(64) NULL,
            fee_model VARCHAR(64) NULL,
            is_backfilled TINYINT NOT NULL DEFAULT 0,
            metric_eligible TINYINT NOT NULL DEFAULT 1,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, event_id),
            INDEX idx_decision_events_user_report
                (userId, source_type, source_report_id),
            INDEX idx_decision_events_user_date
                (userId, decision_date, fund_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observations (
            userId BIGINT NOT NULL,
            observation_id VARCHAR(255) NOT NULL,
            decision_event_id VARCHAR(255) NOT NULL,
            horizon_trading_days INT NOT NULL,
            target_date VARCHAR(16) NULL,
            status VARCHAR(32) NOT NULL,
            is_terminal TINYINT NOT NULL DEFAULT 0,
            revision_no INT NOT NULL DEFAULT 1,
            observed_at VARCHAR(64) NOT NULL,
            finalized_at VARCHAR(64) NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, observation_id),
            UNIQUE KEY uq_outcome_event_horizon
                (userId, decision_event_id, horizon_trading_days),
            INDEX idx_outcome_observations_pending (userId, status, target_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS outcome_observation_revisions (
            userId BIGINT NOT NULL,
            observation_id VARCHAR(255) NOT NULL,
            revision_no INT NOT NULL,
            status VARCHAR(32) NOT NULL,
            is_terminal TINYINT NOT NULL,
            observed_at VARCHAR(64) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, observation_id, revision_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS fund_benchmark_mappings (
            userId BIGINT NOT NULL,
            mapping_id VARCHAR(64) NOT NULL,
            fund_code VARCHAR(32) NOT NULL,
            benchmark_kind VARCHAR(32) NOT NULL,
            completeness VARCHAR(32) NOT NULL,
            benchmark_name VARCHAR(500) NOT NULL,
            benchmark_code VARCHAR(64) NULL,
            valid_from VARCHAR(16) NOT NULL,
            valid_to VARCHAR(16) NULL,
            source VARCHAR(64) NOT NULL,
            source_ref VARCHAR(512) NULL,
            content_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, mapping_id),
            INDEX idx_fund_benchmark_effective
                (userId, fund_code, valid_from, valid_to, benchmark_kind)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_events (
            event_revision_id VARCHAR(64) PRIMARY KEY,
            logical_event_id VARCHAR(255) NOT NULL,
            userId BIGINT NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            revision_no INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            fund_code VARCHAR(32) NULL,
            effective_at VARCHAR(64) NOT NULL,
            recorded_at VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            source VARCHAR(64) NOT NULL,
            source_ref VARCHAR(255) NULL,
            event_hash VARCHAR(64) NOT NULL,
            previous_hash VARCHAR(64) NULL,
            payload_hash VARCHAR(64) NOT NULL,
            payload LONGTEXT NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            UNIQUE KEY uq_portfolio_ledger_logical_revision
                (userId, account_id, logical_event_id, revision_no),
            UNIQUE KEY uq_portfolio_ledger_source_ref
                (userId, account_id, source, source_ref),
            INDEX idx_portfolio_ledger_effective
                (userId, account_id, effective_at, recorded_at, event_revision_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
        """
        CREATE TABLE IF NOT EXISTS portfolio_ledger_heads (
            userId BIGINT NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            revision BIGINT NOT NULL,
            chain_hash VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL,
            PRIMARY KEY (userId, account_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """,
    ]
    for statement in statements:
        cursor.execute(statement)

    # Existing MySQL installations need additive repair because CREATE TABLE IF
    # NOT EXISTS does not add columns introduced by later application versions.
    fetchone = getattr(cursor, "fetchone", None)
    if callable(fetchone):
        transaction_columns = {
            "confirmed_shares": "DOUBLE NULL",
            "fee_yuan": "DOUBLE NULL",
            "shares_source": "VARCHAR(32) NULL",
            "in_progress": "TINYINT NOT NULL DEFAULT 0",
            "confirmed_at": "VARCHAR(64) NULL",
        }
        for column, definition in transaction_columns.items():
            cursor.execute(
                f"""
                SELECT 1 FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'fund_transactions'
                  AND COLUMN_NAME = '{column}'
                """
            )
            if fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE fund_transactions ADD COLUMN {column} {definition}"
                )
        # Ledger versions are content-addressed strings (for example
        # ``pl1:4:abc123``), not counters.  Early v10 DDL declared BIGINT and
        # would reject every real snapshot on MySQL.
        cursor.execute(
            """
            SELECT DATA_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'decision_portfolio_snapshots'
              AND COLUMN_NAME = 'ledger_version'
            """
        )
        ledger_version_column = fetchone()
        if ledger_version_column is not None:
            if isinstance(ledger_version_column, dict):
                ledger_type = str(ledger_version_column.get("DATA_TYPE") or "").lower()
            else:
                ledger_type = str(ledger_version_column[0] or "").lower()
            if ledger_type not in {"varchar", "char", "text", "mediumtext", "longtext"}:
                cursor.execute(
                    "ALTER TABLE decision_portfolio_snapshots "
                    "MODIFY COLUMN ledger_version VARCHAR(128) NULL"
                )
    cursor.execute(
        f"""
        INSERT INTO schema_meta (id, version) VALUES (1, {MYSQL_SCHEMA_VERSION})
        ON DUPLICATE KEY UPDATE version = GREATEST(version, VALUES(version))
        """
    )
    connection.commit()
