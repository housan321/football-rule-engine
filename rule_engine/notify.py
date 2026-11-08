from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from .engine import AnalysisResult


@dataclass
class EmailConfig:
    host: str
    port: int
    mail_from: str
    mail_to: list[str]
    user: Optional[str] = None
    password: Optional[str] = None
    use_tls: bool = True
    use_ssl: bool = False


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _split_addresses(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def email_config_from_env(args=None) -> Optional[EmailConfig]:
    """Build SMTP config from env, optionally overridden by CLI args."""
    host = os.environ.get("SMTP_HOST") or getattr(args, "smtp_host", None)
    raw_port = (
        os.environ.get("SMTP_PORT")
        or getattr(args, "smtp_port", None)
        or 587
    )
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 587
    user = os.environ.get("SMTP_USER") or getattr(args, "smtp_user", None)
    password = os.environ.get("SMTP_PASSWORD") or getattr(
        args, "smtp_password", None
    )
    mail_from = (
        os.environ.get("MAIL_FROM")
        or getattr(args, "mail_from", None)
        or user
    )
    mail_to = _split_addresses(
        os.environ.get("MAIL_TO") or getattr(args, "email_to", None)
    )
    use_ssl = _env_bool("SMTP_SSL", False) or bool(
        getattr(args, "smtp_ssl", False)
    )
    use_tls = (
        not use_ssl
        and _env_bool("SMTP_TLS", True)
        and not getattr(args, "smtp_no_tls", False)
    )
    if not host or not mail_from or not mail_to:
        return None
    return EmailConfig(
        host=host,
        port=port,
        mail_from=mail_from,
        mail_to=mail_to,
        user=user,
        password=password,
        use_tls=use_tls,
        use_ssl=use_ssl,
    )


def format_actionable_body(results: list[AnalysisResult]) -> str:
    rows = [r for r in results if r.actionable]
    lines = [
        f"可下注场次：{len(rows)}",
        "",
        "fixture   联赛        开赛              对阵                                方向             side_line  置信度  操作",
        "--------  ----------  ----------------  ----------------------------------  --------------  ---------  ------  ----",
    ]
    for r in rows:
        match = r.match
        side = f"{r.side_line:+.2f}" if r.side_line is not None else "-"
        lines.append(
            f"{match.fixture_id:<8}  {(match.league or '-'):<10}  "
            f"{match.match_time.strftime('%Y-%m-%d %H:%M'):<16}  "
            f"{(match.home + ' vs ' + match.away):<34}  "
            f"{r.direction_name:<14}  {side:<9}  {r.confidence:<6}  {r.stake}"
        )
    lines += [
        "",
        "说明：side_line 为下注方视角，负数=让球方；仅记录 actionable=是 的场次。",
        "数据来源：亚盘 odds_live_all_raw + 欧赔 odds_history_eu；非投注建议。",
    ]
    return "\n".join(lines)


def send_actionable_email(
    results: list[AnalysisResult],
    config: EmailConfig,
    attachment: tuple[str, bytes] | None = None,
    subject_prefix: str = "[盘口引擎]",
) -> int:
    """Send one email listing only this run's actionable fixtures.

    The optional attachment is a per-run Excel built from the new actionable
    rows; the cumulative paper_tracking.xlsx is never attached.

    Returns the number of actionable fixtures included.
    """
    actionable = [r for r in results if r.actionable]
    if not actionable:
        return 0

    message = EmailMessage()
    message["Subject"] = (
        f"{subject_prefix} {len(actionable)} 场可下注 "
        f"{actionable[0].analysis_time.strftime('%Y-%m-%d %H:%M')}"
    )
    message["From"] = config.mail_from
    message["To"] = ", ".join(config.mail_to)
    message.set_content(format_actionable_body(results))

    if attachment is not None:
        filename, payload = attachment
        message.add_attachment(
            payload,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=filename,
        )

    if config.use_ssl:
        smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=30)
    else:
        smtp = smtplib.SMTP(config.host, config.port, timeout=30)
    try:
        smtp.ehlo()
        if config.use_tls and not config.use_ssl:
            smtp.starttls()
            smtp.ehlo()
        if config.user:
            smtp.login(config.user, config.password or "")
        smtp.send_message(message)
    finally:
        smtp.quit()
    return len(actionable)
