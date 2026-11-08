# -*- coding: utf-8 -*-
"""Scrapy pipeline: titan007 HTML -> MySQL odds_live_all / odds_live_all_raw.

Reuses the tested parser and SQL builder instead of re-implementing persistence:

    scripts/titan007_parser.py::decode_html / parse_html_text
    scripts/import_titan007.py::build_statements
    tools/db.py::get_config / connect

One transaction per fixture; a bad page is dropped without stopping the crawl.
The ``*args`` signatures keep compatibility with both older Scrapy versions
(which pass a spider argument) and Scrapy >= 2.19 (which does not).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

class DropItem(Exception):
    """Local replacement for scrapy.exceptions.DropItem (no Scrapy dependency)."""


from .db import connect, get_config  # noqa: E402
from .parser import decode_html, parse_html_text  # noqa: E402
from .persist import build_statements  # noqa: E402

logger = logging.getLogger(__name__)


class _NullStats:
    def inc_value(self, key, count=1, start=0):
        return None


class Titan007MySQLPipeline:
    def __init__(self, database=None, status_id=1):
        self.database = database
        self.status_id = status_id
        self.cfg = None
        self.conn = None
        self.crawler = None
        self.stats = None

    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls(
            database=crawler.settings.get("TITAN007_DB_NAME") or None,
            status_id=crawler.settings.getint("TITAN007_STATUS_ID", 1),
        )
        pipeline.crawler = crawler
        pipeline.stats = crawler.stats
        return pipeline

    def _stats(self, args):
        if self.stats is not None:
            return self.stats
        spider = args[0] if args else None
        stats = getattr(getattr(spider, "crawler", None), "stats", None)
        return stats or _NullStats()

    # ---- lifecycle -------------------------------------------------

    def open_spider(self, *args):
        self.cfg = get_config()
        self.database = self.database or self.cfg["database"]
        self.conn = connect(self.cfg, database=self.database, autocommit=False)
        logger.info(
            "MySQL pipeline connected: %s@%s:%s/%s",
            self.cfg["user"],
            self.cfg["host"],
            self.cfg["port"],
            self.database,
        )

    def close_spider(self, *args):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    # ---- item handling ---------------------------------------------

    def process_item(self, item, *args):
        stats = self._stats(args)
        try:
            fixture_id = int(item.get("fixture_id"))
        except (TypeError, ValueError) as exc:
            stats.inc_value("titan007/bad_fixture_id")
            raise DropItem(f"invalid fixture id: {item.get('fixture_id')!r}") from exc
        if fixture_id <= 0:
            stats.inc_value("titan007/bad_fixture_id")
            raise DropItem(f"invalid fixture id: {fixture_id}")

        body = item.get("body")
        if not body:
            stats.inc_value("titan007/empty_body")
            raise DropItem(f"fixture {fixture_id}: empty response body")

        try:
            text = decode_html(body, item.get("encoding"))
            data = parse_html_text(text, fixture_id=fixture_id)
        except Exception as exc:
            stats.inc_value("titan007/parse_failures")
            logger.error("fixture %s: parse failed: %s", fixture_id, exc)
            raise DropItem(f"fixture {fixture_id}: parse failed") from exc

        if data.get("fixture_id") is None:
            stats.inc_value("titan007/missing_fixture_id")
            raise DropItem(f"fixture {fixture_id}: parser returned no fixture id")

        if data.get("fixture_id_mismatch"):
            stats.inc_value("titan007/fixture_id_mismatch")
            logger.warning(
                "fixture %s: page embeds id %s; using requested id",
                fixture_id,
                data.get("embedded_fixture_id"),
            )

        if not data.get("main_records"):
            stats.inc_value("titan007/empty_odds")
            raise DropItem(f"fixture {fixture_id}: no main odds records")

        if not data.get("kickoff"):
            logger.warning(
                "fixture %s: kickoff missing; match_list_all will be skipped",
                fixture_id,
            )
        if data.get("home_team_id") is None or data.get("away_team_id") is None:
            logger.warning(
                "fixture %s: team ids missing; match_list_all will be skipped",
                fixture_id,
            )

        try:
            statements, skill_rows, raw_rows = build_statements(
                data, status_id=self.status_id, database=self.database
            )
            if self.conn is None:
                raise RuntimeError("pipeline connection is not open")
            self.conn.ping(reconnect=True)
            with self.conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            self.conn.commit()
        except Exception as exc:
            try:
                if self.conn is not None:
                    self.conn.rollback()
            except Exception:
                pass
            stats.inc_value("titan007/db_failures")
            logger.error(
                "fixture %s: database write failed: %s", fixture_id, exc, exc_info=True
            )
            raise DropItem(f"fixture {fixture_id}: database write failed") from exc

        stats.inc_value("titan007/db_commits")
        stats.inc_value("titan007/rows_main", len(skill_rows))
        stats.inc_value("titan007/rows_raw", len(raw_rows))
        stats.inc_value("titan007/rows_detail", data["counts"]["detail"])
        logger.info(
            "fixture %s: %s vs %s, main=%d raw=%d detail=%d",
            fixture_id,
            data.get("home_team") or "?",
            data.get("away_team") or "?",
            len(skill_rows),
            len(raw_rows),
            data["counts"]["detail"],
        )
        return item
