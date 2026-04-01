"""
CloudCarbon Notification Delivery.

Three delivery channels:
  - Slack Block Kit (POST to webhook URL via httpx)
  - Email (SMTP with plain-text + HTML, credentials from env vars)
  - Generic webhook (POST JSON with HMAC-SHA256 signature header)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx

from agent_types import AgentFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "cloudcarbon@example.com")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://app.cloudcarbon.io")
WEBHOOK_SIGNING_SECRET = os.environ.get("WEBHOOK_SIGNING_SECRET", "")


# ---------------------------------------------------------------------------
# Severity colour mapping
# ---------------------------------------------------------------------------

_SEVERITY_COLORS = {
    "high": "#E53E3E",    # Red
    "medium": "#DD6B20",  # Orange
    "low": "#D69E2E",     # Yellow
}

_SEVERITY_EMOJI = {
    "high": "🔴",
    "medium": "🟠",
    "low": "🟡",
}


# ---------------------------------------------------------------------------
# Slack Block Kit notification
# ---------------------------------------------------------------------------

async def notify_slack(
    webhook_url: str,
    findings: list[AgentFinding],
    agent_type: str,
) -> None:
    """
    Send a Slack Block Kit message summarising agent findings.

    Structure:
      - Header: "CloudCarbon Alert — {agent_type}"
      - For each finding: service, metric, current value, baseline, severity badge
      - Footer: link to dashboard
    """
    if not findings:
        return

    # Determine overall severity
    severities = [f.severity for f in findings]
    overall_severity = (
        "high" if "high" in severities
        else "medium" if "medium" in severities
        else "low"
    )
    color = _SEVERITY_COLORS.get(overall_severity, "#718096")
    emoji = _SEVERITY_EMOJI.get(overall_severity, "⚪")

    # Build blocks
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} CloudCarbon Alert — {agent_type.replace('_', ' ').title()}",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{len(findings)} finding{'s' if len(findings) != 1 else ''} detected*",
            },
        },
        {"type": "divider"},
    ]

    # Add a block for each finding (max 20 to avoid Slack limits)
    for finding in findings[:20]:
        sev_emoji = _SEVERITY_EMOJI.get(finding.severity, "⚪")
        baseline_text = (
            f" (baseline: {finding.baseline_mean:.2f})"
            if finding.baseline_mean is not None
            else ""
        )
        zscore_text = (
            f" | z-score: {finding.z_score:.1f}"
            if finding.z_score is not None
            else ""
        )
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Service:* {finding.service_name}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Region:* {finding.region}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Metric:* {finding.metric}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*Value:* {finding.current_value:.2f}"
                            f"{baseline_text}{zscore_text}"
                        ),
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:* {sev_emoji} {finding.severity.upper()}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Detail:* {finding.description[:200]}",
                    },
                ],
            }
        )

    if len(findings) > 20:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_... and {len(findings) - 20} more findings_",
                },
            }
        )

    # Footer
    blocks.extend(
        [
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{DASHBOARD_URL}|View in CloudCarbon Dashboard>",
                },
            },
        ]
    )

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": blocks,
            }
        ]
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(webhook_url, json=payload)
        response.raise_for_status()

    logger.info(
        "Slack notification sent for %s (%d findings)",
        agent_type,
        len(findings),
    )


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

async def notify_email(
    address: str,
    findings: list[AgentFinding],
    agent_type: str,
) -> None:
    """
    Send a plain-text + HTML email summarising agent findings.
    Uses SMTP credentials from environment variables.
    """
    if not findings:
        return

    severities = [f.severity for f in findings]
    overall_severity = (
        "HIGH" if "high" in severities
        else "MEDIUM" if "medium" in severities
        else "LOW"
    )

    subject = (
        f"CloudCarbon — {overall_severity} alert from "
        f"{agent_type.replace('_', ' ').title()}"
    )

    # Plain text body
    plain_lines = [
        f"CloudCarbon Alert — {agent_type.replace('_', ' ').title()}",
        f"Severity: {overall_severity}",
        f"Findings: {len(findings)}",
        "",
    ]
    for f in findings:
        plain_lines.append(
            f"[{f.severity.upper()}] {f.service_name} / {f.region} — "
            f"{f.metric}: {f.current_value:.2f}"
        )
        plain_lines.append(f"  {f.description}")
        plain_lines.append("")

    plain_lines.append(f"View in dashboard: {DASHBOARD_URL}")
    plain_text = "\n".join(plain_lines)

    # HTML body
    rows_html = ""
    for f in findings:
        color = _SEVERITY_COLORS.get(f.severity, "#718096")
        rows_html += (
            f"<tr>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{f.service_name}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{f.region}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{f.metric}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee'>{f.current_value:.2f}</td>"
            f"<td style='padding:8px;border-bottom:1px solid #eee;color:{color}'>"
            f"<strong>{f.severity.upper()}</strong></td>"
            f"</tr>"
        )

    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px">
  <h2 style="color:#2D3748">
    ☁️ CloudCarbon Alert — {agent_type.replace('_', ' ').title()}
  </h2>
  <p><strong>Overall Severity:</strong>
    <span style="color:{_SEVERITY_COLORS.get(overall_severity.lower(), '#718096')}">
      {overall_severity}
    </span>
  </p>
  <p><strong>{len(findings)} finding{'s' if len(findings) != 1 else ''} detected</strong></p>
  <table style="width:100%;border-collapse:collapse;margin-top:16px">
    <thead>
      <tr style="background:#EDF2F7">
        <th style="padding:8px;text-align:left">Service</th>
        <th style="padding:8px;text-align:left">Region</th>
        <th style="padding:8px;text-align:left">Metric</th>
        <th style="padding:8px;text-align:left">Value</th>
        <th style="padding:8px;text-align:left">Severity</th>
      </tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <p style="margin-top:24px">
    <a href="{DASHBOARD_URL}" style="color:#3182CE">View in CloudCarbon Dashboard →</a>
  </p>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = address
    msg.attach(MIMEText(plain_text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            if SMTP_PORT == 587:
                smtp.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.sendmail(SMTP_FROM, [address], msg.as_string())
        logger.info(
            "Email notification sent to %s for %s (%d findings)",
            address,
            agent_type,
            len(findings),
        )
    except Exception as exc:
        logger.exception("Email notification failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Generic webhook notification
# ---------------------------------------------------------------------------

async def notify_webhook(
    url: str,
    payload: dict[str, Any],
    signing_secret: str = "",
) -> None:
    """
    POST findings as JSON to a webhook URL.
    Includes HMAC-SHA256 signature in X-CloudCarbon-Signature header.
    """
    body = json.dumps(payload, default=str).encode("utf-8")

    # Use tenant-specific secret if provided, fall back to global secret
    secret = signing_secret or WEBHOOK_SIGNING_SECRET
    timestamp = str(int(time.time()))

    if secret:
        sig_payload = f"{timestamp}.".encode() + body
        signature = hmac.new(
            secret.encode("utf-8"),
            sig_payload,
            hashlib.sha256,
        ).hexdigest()
        sig_header = f"t={timestamp},v1={signature}"
    else:
        sig_header = f"t={timestamp},v1=unsigned"

    headers = {
        "Content-Type": "application/json",
        "X-CloudCarbon-Signature": sig_header,
        "X-CloudCarbon-Agent": payload.get("agent_type", "unknown"),
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, content=body, headers=headers)
        response.raise_for_status()

    logger.info(
        "Webhook notification sent to %s for agent %s",
        url,
        payload.get("agent_type", "unknown"),
    )
