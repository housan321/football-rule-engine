from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch

from rule_engine.engine import AnalysisResult
from rule_engine.models import Match
from rule_engine.notify import (
    EmailConfig,
    email_config_from_env,
    format_actionable_body,
    send_actionable_email,
)


def make_result(actionable: bool = True) -> AnalysisResult:
    match = Match(
        fixture_id=123,
        match_time=datetime(2026, 11, 8, 12, 0),
        league="测试联赛",
        home="主队",
        away="客队",
    )
    return AnalysisResult(
        match=match,
        mode="pre",
        analysis_time=datetime(2026, 11, 8, 11, 0),
        direction="home",
        confidence=55,
        confidence_label="中低",
        stake="小注" if actionable else "仅观察",
        score_home=2.0,
        score_away=0.0,
        conflict_ratio=0.0,
        summary="",
        features={"asian": {"latest_line": -0.5}},
        side_line=-0.5,
        is_giving=True,
        experimental_default=actionable,
        actionable=actionable,
    )


class NotifyTests(unittest.TestCase):
    def test_body_only_actionable(self):
        body = format_actionable_body([make_result(True), make_result(False)])
        self.assertIn("可下注场次：1", body)
        self.assertIn("123", body)
        self.assertIn("-0.50", body)

    def test_config_from_env(self):
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "user@example.com",
            "SMTP_PASSWORD": "secret",
            "MAIL_FROM": "user@example.com",
            "MAIL_TO": "a@example.com,b@example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = email_config_from_env()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.host, "smtp.example.com")
        self.assertEqual(cfg.port, 587)
        self.assertEqual(cfg.mail_to, ["a@example.com", "b@example.com"])
        self.assertTrue(cfg.use_tls)

    def test_missing_config(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(email_config_from_env())

    def test_no_actionable_sends_nothing(self):
        cfg = EmailConfig(
            host="127.0.0.1", port=25, mail_from="a@example.com",
            mail_to=["b@example.com"],
        )
        self.assertEqual(
            send_actionable_email(
                [make_result(False)], "tracking/none.xlsx", cfg
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
