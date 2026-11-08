-- ============================================================
-- 足球盘口分析 · 自建 MySQL 数据库结构
-- 适用：MySQL 8.0+ / MariaDB 10.5+
-- 说明：
--   odds_live_all     只存“主盘 + 赛前”数据，保持与 skill 的 SQL 兼容
--   odds_live_all_raw 存全部盘口线（盘2/3/4）和走地盘，供扩展分析
-- ============================================================

CREATE DATABASE IF NOT EXISTS football
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE football;

SET NAMES utf8mb4;

-- ------------------------------------------------------------
-- 1. 球队字典
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_list (
  team_id     BIGINT       NOT NULL,
  name_zh     VARCHAR(100) NOT NULL,
  name_en     VARCHAR(100) NULL,
  updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (team_id),
  UNIQUE KEY uk_team_name_zh (name_zh)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 2. 联赛字典
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS competition_list (
  competition_id BIGINT       NOT NULL,
  name_zh        VARCHAR(100) NOT NULL,
  name_en        VARCHAR(100) NULL,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (competition_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 3. 比赛主表
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_list_all (
  fixture_id     BIGINT    NOT NULL,
  competition_id BIGINT    NOT NULL,
  home_team_id   BIGINT    NOT NULL,
  away_team_id   BIGINT    NOT NULL,
  match_time     DATETIME  NOT NULL,
  status_id      INT       NOT NULL DEFAULT 1,
  home_score     INT       NULL,
  away_score     INT       NULL,
  source         VARCHAR(32) NULL,
  updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (fixture_id),
  KEY idx_match_time (match_time),
  KEY idx_match_teams (home_team_id, away_team_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 4. 亚盘数据（skill 直接使用）
--    约定：
--      line_no = 0        主盘
--      phase   = prematch 赛前
--      handicap 负数 = 主队让球，正数 = 主队受让
--      home_odds / away_odds = 水位，不是欧赔
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS odds_live_all (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  fixture_id      BIGINT       NOT NULL,
  hometeam        VARCHAR(100) NULL,
  awayteam        VARCHAR(100) NULL,
  league_id       BIGINT       NULL,
  league          VARCHAR(100) NULL,
  company_id      INT          NOT NULL,
  company_name    VARCHAR(50)  NULL,
  company_id_titan INT         NULL,
  line_no         TINYINT      NOT NULL DEFAULT 0,
  phase           VARCHAR(16)  NOT NULL DEFAULT 'prematch',
  change_the_time DATETIME     NOT NULL,
  home_odds       DECIMAL(6,3) NOT NULL,
  handicap        DECIMAL(5,2) NOT NULL,
  away_odds       DECIMAL(6,3) NOT NULL,
  source          VARCHAR(32)  NOT NULL DEFAULT 'titan007',
  row_hash        CHAR(64)     NOT NULL,
  ingested_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_odds_row_hash (row_hash),
  KEY idx_fixture_company_time (fixture_id, company_id, change_the_time),
  KEY idx_fixture_line_phase (fixture_id, line_no, phase)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 5. 亚盘原始数据（全部盘口线 + 走地）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS odds_live_all_raw (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  fixture_id      BIGINT       NOT NULL,
  hometeam        VARCHAR(100) NULL,
  awayteam        VARCHAR(100) NULL,
  league_id       BIGINT       NULL,
  league          VARCHAR(100) NULL,
  company_id      INT          NOT NULL,
  company_name    VARCHAR(50)  NULL,
  company_id_titan INT         NULL,
  line_no         TINYINT      NOT NULL DEFAULT 0,
  phase           VARCHAR(16)  NOT NULL DEFAULT 'prematch',
  change_the_time DATETIME     NOT NULL,
  home_odds       DECIMAL(6,3) NOT NULL,
  handicap        DECIMAL(5,2) NOT NULL,
  away_odds       DECIMAL(6,3) NOT NULL,
  score           VARCHAR(20)  NULL,
  source          VARCHAR(32)  NOT NULL DEFAULT 'titan007',
  row_hash        CHAR(64)     NOT NULL,
  ingested_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_raw_row_hash (row_hash),
  KEY idx_raw_fixture_company_time (fixture_id, company_id, change_the_time),
  KEY idx_raw_fixture_line_phase (fixture_id, line_no, phase)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 6. 欧赔历史（titan007 的 1x2 页面未来接入）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS odds_history_eu (
  id              BIGINT       NOT NULL AUTO_INCREMENT,
  fixture_id      BIGINT       NOT NULL,
  hometeam        VARCHAR(100) NULL,
  awayteam        VARCHAR(100) NULL,
  league_id       BIGINT       NULL,
  league          VARCHAR(100) NULL,
  company_id      INT          NOT NULL,
  company_name    VARCHAR(50)  NULL,
  change_the_time DATETIME     NOT NULL,
  home_win        DECIMAL(6,2) NOT NULL,
  draw            DECIMAL(6,2) NOT NULL,
  away_win        DECIMAL(6,2) NOT NULL,
  source          VARCHAR(32)  NOT NULL DEFAULT 'titan007',
  row_hash        CHAR(64)     NOT NULL,
  ingested_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_eu_row_hash (row_hash),
  KEY idx_eu_fixture_company_time (fixture_id, company_id, change_the_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 7. 公司映射（titan007 -> skill 的 company_id）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS company_map (
  company_id_titan INT         NOT NULL,
  company_id       INT         NOT NULL,
  company_name     VARCHAR(50) NOT NULL,
  note             VARCHAR(200) NULL,
  PRIMARY KEY (company_id_titan),
  KEY idx_company_id (company_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO company_map (company_id_titan, company_id, company_name, note) VALUES
  (1,  7,  '澳门',       'skill 最高权威'),
  (3,  3,  '皇冠',       '亚洲市场参考'),
  (8,  2,  'Bet365',     '主流参考'),
  (12, 10, '易胜博',     NULL),
  (14, 11, '韦德',       NULL),
  (17, 6,  '明陞',       NULL),
  (22, 4,  '10BET',      NULL),
  (24, 14, '12bet',      NULL),
  (31, 15, '利记',       NULL),
  (35, 16, '盈禾',       NULL),
  (42, 17, '18Bet',      NULL),
  (47, 22, '平博',       'Pinnacle，真实概率参考'),
  (9,  9,  '威廉希尔',   '欧洲主流'),
  (19, 13, 'Interwetten', NULL),
  (48, 23, '香港马会',   '自定义 ID'),
  (50, 24, '1xBet',      'skill 明确排除，仅存档')
ON DUPLICATE KEY UPDATE
  company_id = VALUES(company_id),
  company_name = VALUES(company_name),
  note = VALUES(note);

-- ------------------------------------------------------------
-- 8. 导入日志
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_import_log (
  id                  BIGINT      NOT NULL AUTO_INCREMENT,
  source              VARCHAR(32) NOT NULL,
  fixture_id          BIGINT      NULL,
  file_name           VARCHAR(255) NULL,
  rows_main           INT         NOT NULL DEFAULT 0,
  rows_detail_prematch INT        NOT NULL DEFAULT 0,
  rows_inserted       INT         NOT NULL DEFAULT 0,
  imported_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_import_fixture (fixture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 9. 实力参数（与原 skill 的 modeling.team_params 对齐）
--    scripts/build_team_params.py 从历史赛果拟合 ave_sup
-- ------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS modeling;

CREATE TABLE IF NOT EXISTS modeling.team_params (
  team_id      BIGINT       NOT NULL,
  teamname     VARCHAR(100) NULL,
  comp_id      BIGINT       NOT NULL,
  fit_end_date DATE         NOT NULL,
  ave_sup      DECIMAL(6,3) NOT NULL,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (team_id, comp_id, fit_end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 10. 便捷视图（可选）
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW odds_live_main AS
SELECT fixture_id, company_id, company_name, change_the_time,
       home_odds, handicap, away_odds
FROM odds_live_all
WHERE line_no = 0 AND phase = 'prematch';
