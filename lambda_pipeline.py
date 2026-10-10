"""
Insights vs NationBuilder comparison — AWS Lambda entry point.

Runs the full comparison pipeline in a single process:

  1. Pull A — the "Zip State Federal District" Insights view (Tableau REST API).
  2. Pull B — every signup from the NationBuilder V2 API (OAuth 2.0, parallel),
     rotating the refresh token back into Secrets Manager after the run.
  3. Compare — inner-join on signup_id and flag per-field matches for
     registered_state / registered_zip / federal_district.
  4. Write the comparison + mismatches reports (CSV + XLSX) to OUTPUT_DIR.
  5. (Optional) upload every report to S3 (date-stamped) and email the mismatches
     report as attachments via SES.

The same `run()` works locally and in Lambda; every cloud feature is a no-op
when its environment variable is unset, so a local run writes files only.

Local entry point:  `main()`.
AWS Lambda entry point:  `handler(event, context)`.

Credentials come from the environment (local `.env`) or, in Lambda, from an AWS
Secrets Manager secret whose JSON keys are the `NATIONBUILDER_*` (V2 OAuth) and
`NB_INSIGHTS_*` (Tableau) variable names. Both secret loaders below and
`nb_insights._load_secret_into_env()` can point at the SAME secret id.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load .env for local runs (no-op in Lambda). Must precede reading env vars and
# importing nb_insights (which loads its own Insights secret at import time).
load_dotenv()


# ---------------------------------------------------------------------------
# Secrets Manager — load V2 OAuth credentials and rotate the refresh token
# ---------------------------------------------------------------------------
def _load_v2_secret_into_env() -> None:
    """Populate NATIONBUILDER_* env vars from Secrets Manager, if configured.

    When NATIONBUILDER_SECRET_ID is set (name or ARN), the secret's JSON is read
    and each key copied into os.environ WITHOUT overwriting an existing value —
    so local `.env`/shell values win, and in Lambda the secret supplies the
    OAuth credentials. No-op when unset.

    The Insights (`NB_INSIGHTS_*`) credentials are loaded separately by
    `nb_insights._load_secret_into_env()` at import; both may point at the same
    secret id, and each loader only consumes the keys it recognizes.
    """
    secret_id = os.getenv("NATIONBUILDER_SECRET_ID")
    if not secret_id:
        return

    import boto3  # lazy; only needed when a secret is configured

    region = os.getenv("NATIONBUILDER_SECRET_REGION") or os.getenv("AWS_REGION")
    client = boto3.client("secretsmanager", region_name=region or None)
    raw = client.get_secret_value(SecretId=secret_id).get("SecretString") or ""
    raw = raw.lstrip("\ufeff")  # tolerate a UTF-8 BOM
    if not raw.strip():
        raise RuntimeError(
            f"Secret '{secret_id}' returned an empty value; expected a JSON "
            f"object of NATIONBUILDER_* / NB_INSIGHTS_* keys."
        )
    for key, value in json.loads(raw).items():
        os.environ.setdefault(key, str(value))


_load_v2_secret_into_env()

# nb_insights loads its own Insights secret (NB_INSIGHTS_SECRET_ID) at import.
# Import it AFTER _load_v2_secret_into_env so a single combined secret is already
# in the environment when nb_insights reads it.
import pull_insights  # noqa: E402
import pull_nationbuilder  # noqa: E402
from compare_pipelines import (  # noqa: E402
    COMPARE_FIELDS,
    build_comparison,
    mismatches_only,
    write_comparison_files,
)


def _persist_refresh_token(new_refresh: str) -> None:
    """Persist a rotated V2 refresh token.

    In Lambda (NATIONBUILDER_SECRET_ID set) write it back into the JSON secret so
    the next scheduled run can use it — /tmp and .env are not durable there.
    Locally (no secret id) fall back to rewriting .env.
    """
    secret_id = os.getenv("NATIONBUILDER_SECRET_ID")
    if secret_id:
        import boto3

        region = os.getenv("NATIONBUILDER_SECRET_REGION") or os.getenv("AWS_REGION")
        sm = boto3.client("secretsmanager", region_name=region or None)
        raw = sm.get_secret_value(SecretId=secret_id).get("SecretString") or "{}"
        data = json.loads(raw.lstrip("\ufeff") or "{}")
        data["NATIONBUILDER_REFRESH_TOKEN"] = new_refresh
        sm.put_secret_value(SecretId=secret_id, SecretString=json.dumps(data))
    else:
        from nationbuilder_v2.client import _persist_refresh_token_to_env

        _persist_refresh_token_to_env(new_refresh)
    os.environ["NATIONBUILDER_REFRESH_TOKEN"] = new_refresh


# ---------------------------------------------------------------------------
# Configuration (env-driven; every cloud feature is a no-op when unset)
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.getenv("OUTPUT_DIR", ".")

S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = os.getenv("S3_PREFIX", "")
S3_REGION = os.getenv("S3_REGION", "")
PRESIGN_EXPIRY_SECONDS = int(os.getenv("PRESIGN_EXPIRY_SECONDS", str(7 * 24 * 3600)))

SES_SENDER = os.getenv("SES_SENDER", "")
SES_REGION = os.getenv("SES_REGION", "") or S3_REGION
RECIPIENTS_S3_URI = os.getenv("RECIPIENTS_S3_URI", "")
RECIPIENTS_FILE = os.getenv("RECIPIENTS_FILE", "recipients.txt")

NB_CONCURRENCY = int(os.getenv("NB_LOOKUP_CONCURRENCY", "20"))

DEFAULT_EMAIL_MESSAGE = (
    "This report lists every signup_id present in BOTH NationBuilder Insights "
    "and the NationBuilder V2 API whose registered state, ZIP, or federal "
    "district disagree between the two systems — i.e. where Insights has fallen "
    "out of sync with NationBuilder.\n\n"
    "Each report is provided as both a CSV and an XLSX file. This is because of "
    "ZIP codes with leading zeros (e.g. 01002). The CSV stores the ZIP as text "
    "so the leading zero is preserved everywhere (text editors, Google Sheets, "
    "pandas), but Excel will strip it if you open the CSV directly. The XLSX "
    "version types the ZIP column as text so Excel keeps the leading zero. Use "
    "the XLSX in Excel; the CSV is the universal form for everything else."
)
EMAIL_MESSAGE = os.getenv("EMAIL_MESSAGE", DEFAULT_EMAIL_MESSAGE)
EMAIL_SUBJECT = "Insights vs NationBuilder comparison report"

# Output filenames (must match compare_pipelines).
COMPARISON_CSV = "comparison.csv"
COMPARISON_XLSX = "comparison.xlsx"
MISMATCHES_CSV = "comparison_mismatches.csv"
MISMATCHES_XLSX = "comparison_mismatches.xlsx"
STATUS_FILE = "lambda_pipeline.status.txt"

# Emailed as attachments: the mismatches report (the actionable one) plus the
# full comparison CSV for reference. The full comparison XLSX is uploaded to S3
# but not emailed (it is the largest file and duplicates the CSV).
EMAIL_INCLUDE_FILES = {MISMATCHES_CSV, MISMATCHES_XLSX, COMPARISON_CSV}

RUN_DATE = datetime.now(timezone.utc).strftime("%Y%m%d")
# SES caps a message at ~10 MB; base64 inflates ~37%, so keep a raw budget.
SES_MAX_ATTACH_BYTES = 7_000_000


def _out(filename: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, filename)


def _dated_name(filename: str) -> str:
    """Insert the run date before the extension: foo.csv -> foo_YYYYMMDD.csv."""
    stem, ext = os.path.splitext(filename)
    return f"{stem}_{RUN_DATE}{ext}"


def _write_status(lines: list[str]) -> None:
    with open(_out(STATUS_FILE), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    without = uri[len("s3://"):]
    bucket, _, key = without.partition("/")
    return bucket, key


# ---------------------------------------------------------------------------
# S3 upload + SES email (no-ops when their env vars are unset)
# ---------------------------------------------------------------------------
def upload_to_s3(paths: list[str]) -> list[str]:
    """Upload each file to S3 with a date-stamped object name; return presigned URLs."""
    if not S3_BUCKET:
        return []
    import boto3

    s3 = boto3.client("s3", region_name=S3_REGION or None)
    prefix = S3_PREFIX.strip("/")
    urls: list[str] = []
    for path in paths:
        object_name = _dated_name(os.path.basename(path))
        key = f"{prefix}/{object_name}" if prefix else object_name
        s3.upload_file(path, S3_BUCKET, key)
        urls.append(
            s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": S3_BUCKET, "Key": key},
                ExpiresIn=PRESIGN_EXPIRY_SECONDS,
            )
        )
    return urls


def load_recipients() -> list[str]:
    """Email recipients from S3 (RECIPIENTS_S3_URI) or local RECIPIENTS_FILE."""
    text = ""
    if RECIPIENTS_S3_URI:
        import boto3

        bucket, key = _parse_s3_uri(RECIPIENTS_S3_URI)
        obj = boto3.client("s3", region_name=S3_REGION or None).get_object(
            Bucket=bucket, Key=key
        )
        text = obj["Body"].read().decode("utf-8")
    elif os.path.exists(RECIPIENTS_FILE):
        with open(RECIPIENTS_FILE, encoding="utf-8") as fh:
            text = fh.read()
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _email_body(summary: dict, attached: list[str], skipped: list[str]) -> str:
    counts = summary.get("field_mismatches", {})
    lines = [
        "The Insights vs NationBuilder comparison run has completed.",
        "",
        f"signup_ids compared (in both):   {summary.get('compared', 'n/a')}",
        f"Records with any mismatch:        {summary.get('mismatches', 'n/a')}",
        f"  registered_state mismatches:    {counts.get('registered_state', 'n/a')}",
        f"  registered_zip mismatches:      {counts.get('registered_zip', 'n/a')}",
        f"  federal_district mismatches:    {counts.get('federal_district', 'n/a')}",
    ]
    if EMAIL_MESSAGE.strip():
        lines += ["", EMAIL_MESSAGE.strip()]
    if attached:
        lines += ["", "Attached:"] + [f"  {n}" for n in attached]
    if skipped:
        lines += ["", "Not attached (too large; in S3):"] + [f"  {n}" for n in skipped]
    lines += ["", "These files contain member PII. Do not forward this email."]
    return "\n".join(lines)


def notify_skip_by_email() -> list[str]:
    """Email a short 'run skipped — Insights returned no data' notice via SES.

    Used when Pull A comes back empty so the skipped run is an actionable alert
    rather than a silent no-op. No-op (returns []) when SES_SENDER is unset or
    no recipients are configured.
    """
    if not SES_SENDER:
        return []
    recipients = load_recipients()
    if not recipients:
        return []

    import boto3

    body = (
        "The Insights vs NationBuilder comparison run was SKIPPED.\n\n"
        "The NationBuilder Insights view returned 0 rows, so there was nothing "
        "to compare and no report was produced. This usually means the Insights "
        "workbook behind the view is mid-rebuild, empty, or has been retired on "
        "the Insights side.\n\n"
        "No action is needed if an Insights refresh is in progress; the next "
        "scheduled run will pick the data up automatically once it returns. If "
        "the view has moved, the VIEW_ID in pull_insights.py needs updating.\n"
    )
    boto3.client("ses", region_name=SES_REGION or None).send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": recipients},
        Message={
            "Subject": {"Data": f"{EMAIL_SUBJECT} — SKIPPED (no Insights data)"},
            "Body": {"Text": {"Data": body}},
        },
    )
    return recipients


def notify_by_email(files: list[str], summary: dict) -> list[str]:
    """Email the comparison reports as attachments via SES. Returns recipients.

    No-op (returns []) when SES_SENDER is unset or no recipients are found. Only
    the files in EMAIL_INCLUDE_FILES are attached, within the SES size budget.
    """
    if not SES_SENDER:
        return []
    recipients = load_recipients()
    if not recipients:
        return []

    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    import boto3

    attach_paths = [p for p in files if os.path.basename(p) in EMAIL_INCLUDE_FILES]
    attached_names, skipped_names, parts, total = [], [], [], 0
    for path in attach_paths:
        size = os.path.getsize(path)
        dated = _dated_name(os.path.basename(path))
        if total + size > SES_MAX_ATTACH_BYTES:
            skipped_names.append(dated)
            continue
        with open(path, "rb") as fh:
            part = MIMEApplication(fh.read())
        part.add_header("Content-Disposition", "attachment", filename=dated)
        parts.append(part)
        attached_names.append(dated)
        total += size

    msg = MIMEMultipart()
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = SES_SENDER
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_email_body(summary, attached_names, skipped_names)))
    for part in parts:
        msg.attach(part)

    boto3.client("ses", region_name=SES_REGION or None).send_raw_email(
        Source=SES_SENDER,
        Destinations=recipients,
        RawMessage={"Data": msg.as_string()},
    )
    return recipients


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run() -> dict:
    """Pull both sources, compare, write reports, optionally upload + email.

    Never raises: any failure is captured in the returned dict and the status
    file, so a Lambda always gets a clean JSON-serializable response.
    """
    lines: list[str] = []
    result: dict = {"status": "ERROR", "files": [], "presigned_urls": []}
    try:
        # 1. Pull A — Insights (Tableau).
        lines.append("Pull A: NationBuilder Insights view ...")
        insights_df = pull_insights.pull_insights()
        lines.append(f"  Insights signups: {len(insights_df):,}")

        # Guard: the Insights view sometimes returns zero rows (e.g. the
        # underlying workbook is mid-rebuild or has been emptied/retired on the
        # Insights side). Comparing against an empty Insights pull is meaningless
        # and would otherwise blow up downstream, so stop here, email a short
        # heads-up, and return a distinct SKIPPED status (not a silent failure).
        if len(insights_df) == 0:
            lines.append("")
            lines.append(
                "Insights view returned 0 rows — skipping the comparison. "
                "The Insights workbook may be mid-rebuild or empty."
            )
            result.update(status="SKIPPED", compared=0, mismatches=0, files=[])
            if SES_SENDER:
                emailed = notify_skip_by_email()
                result["emailed_to"] = emailed
                lines.append(
                    f"Emailed skip notice to: {', '.join(emailed)}"
                    if emailed else "Skip-notice email skipped (no recipients)."
                )
            lines.append("STATUS: SKIPPED")
            _write_status(lines)
            print("\n".join(lines))
            return result

        # 2. Pull B — NationBuilder V2 (parallel; rotate refresh token on finish).
        lines.append("Pull B: NationBuilder V2 API ...")
        nb_df = pull_nationbuilder.pull_full(
            concurrency=NB_CONCURRENCY, on_refresh=_persist_refresh_token
        )
        lines.append(f"  NationBuilder signups: {len(nb_df):,}")

        # 3. Compare in-process.
        comparison = build_comparison(insights_df=insights_df, nationbuilder_df=nb_df)
        mismatches = mismatches_only(comparison)

        field_mismatches = {
            f: int((comparison[f"{f}_match"] == "MISMATCH").sum())
            for f in COMPARE_FIELDS
        }
        lines.append("")
        lines.append(f"Compared {len(comparison):,} signup_ids present in both.")
        for f in COMPARE_FIELDS:
            lines.append(f"  {f} mismatches: {field_mismatches[f]:,}")
        lines.append(f"Records with any mismatch: {len(mismatches):,}")

        # 4. Write reports.
        written = write_comparison_files(comparison)
        lines.append("")
        lines.append(f"Wrote {len(written)} files to {OUTPUT_DIR}:")
        lines += [f"  {os.path.basename(p)}" for p in written]

        result.update(
            status="OK",
            compared=int(len(comparison)),
            mismatches=int(len(mismatches)),
            field_mismatches=field_mismatches,
            files=written,
        )

        # 5a. Upload to S3 (date-stamped).
        if S3_BUCKET:
            urls = upload_to_s3(written)
            result["presigned_urls"] = urls
            lines.append("")
            lines.append(
                f"Uploaded {len(urls)} dated file(s) to "
                f"s3://{S3_BUCKET}/{S3_PREFIX.strip('/')} (run date {RUN_DATE})."
            )

        # 5b. Email the reports as attachments.
        if SES_SENDER:
            emailed = notify_by_email(written, result)
            result["emailed_to"] = emailed
            lines.append("")
            lines.append(
                f"Emailed report attachments to: {', '.join(emailed)}"
                if emailed else "Email skipped (no recipients found)."
            )

        lines.append("STATUS: OK")
    except Exception as exc:  # noqa: BLE001 - surface any error to the status file
        import traceback

        result["error"] = repr(exc)
        lines.append("STATUS: ERROR")
        lines.append(repr(exc))
        lines.append(traceback.format_exc())

    _write_status(lines)
    print("\n".join(lines))
    return result


def main() -> None:
    """Local entry point."""
    run()


def handler(event, context):  # noqa: ANN001 - Lambda signature
    """AWS Lambda entry point. Returns a JSON-serializable run summary."""
    result = run()
    # OK and SKIPPED are both clean, expected outcomes (200); only a real
    # exception (ERROR) is a 500.
    status_code = 200 if result.get("status") in ("OK", "SKIPPED") else 500
    return {"statusCode": status_code, "result": result}


if __name__ == "__main__":
    main()
