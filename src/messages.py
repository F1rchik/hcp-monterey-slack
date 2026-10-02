"""Slack message bodies, in the manager's formats (Slack thread with Assiya, 2026-09-24).

Each builder returns (text, blocks). `text` is the notification/fallback text and
is what a dry run prints; `blocks` is what the channel shows.
"""

from __future__ import annotations

from datetime import date

from rules import (CHECK, CROSS, ApprovedOption, JobLine, LeadSource, customer_name,
                   customer_phone, money)


def mention(employee: dict, slack_ids: dict[str, str]) -> str:
    slack_id = slack_ids.get(str(employee.get("id")))
    return f"<@{slack_id}>" if slack_id else f"*{customer_name(employee)}*"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _link_button(label: str, url: str) -> dict:
    return {"type": "actions", "elements": [{
        "type": "button", "text": {"type": "plain_text", "text": label}, "url": url,
    }]}


def approved_estimate(item: ApprovedOption, description: str, slack_ids: dict[str, str],
                      links: dict[str, str]) -> tuple[str, list[dict]]:
    est, opt = item.estimate, item.option
    estimators = ", ".join(mention(e, slack_ids) for e in est.get("assigned_employees") or []) or "—"
    lines = [":bell: *APPROVED ESTIMATE* :bell:"]
    if description:
        lines.append(f"*{description}*")
    lines += [
        f"*Customer Name:* {customer_name(est.get('customer'))}",
        f"*Estimate Number:* {est.get('estimate_number') or '—'}",
        f"*Amount:* {money(opt.get('total_amount'))}",
        f"*Estimator:* {estimators}",
    ]
    text = "\n".join(lines)
    blocks = [_section(text)]
    if links.get("estimate") and est.get("id"):
        blocks.append(_link_button("Estimate link", links["estimate"].format(id=est["id"])))
    return text, blocks


def new_lead(lead: dict, source: LeadSource, service: str,
             links: dict[str, str]) -> tuple[str, list[dict]]:
    customer = lead.get("customer")
    lines = [f"{source.emoji} *New Lead from {source.label}*".strip(),
             f"*Name:* {customer_name(customer)}"]
    phone = customer_phone(customer)
    if phone:  # "Phone: если есть"
        lines.append(f"*Phone:* {phone}")
    lines.append(f"*Service:* {service or '—'}")
    text = "\n".join(lines)
    blocks = [_section(text)]
    if links.get("lead") and lead.get("id"):
        blocks.append(_link_button("Lead link", links["lead"].format(id=lead["id"])))
    return text, blocks


def _mark(ok: bool) -> str:
    return CHECK if ok else CROSS


def _job_block(line: JobLine, links: dict[str, str]) -> str:
    number = f"#{line.number}" if line.number else "(no number)"
    if links.get("job") and line.job.get("id"):
        number = f"<{links['job'].format(id=line.job['id'])}|{number}>"
    first = f"{line.first_label}: {_mark(line.first_ok)}"
    if line.first_note:
        first += f" {line.first_note}"
    return "\n".join([
        f"*{number} — {line.customer}*",
        first,
        f"Photos: {_mark(line.photos)}",
        f"Paid: {_mark(line.paid)}" + (f" {line.paid_note}" if line.paid_note else ""),
    ])


def daily_report(who: str, day: date, lines: list[JobLine],
                 links: dict[str, str]) -> tuple[str, list[dict]]:
    header = f"{who} Jobs — {day.strftime('%A, %B')} {day.day}"
    bodies = [_job_block(l, links) for l in lines]
    text = "\n\n".join([header, *bodies])
    # One section per job: a section caps at 3000 chars, a busy day would not fit.
    blocks = [_section(header)] + [_section(b) for b in bodies]
    return text, blocks[:50]
