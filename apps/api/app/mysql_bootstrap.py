from __future__ import annotations

from typing import Any


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
    ]
    for statement in statements:
        cursor.execute(statement)
    cursor.execute(
        "INSERT IGNORE INTO schema_meta (id, version) VALUES (1, 2)"
    )
    connection.commit()
