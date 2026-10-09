# Deploying the comparison pipeline as an AWS Lambda

This function runs the **Insights vs NationBuilder comparison** end to end:

1. Pull A — the "Zip State Federal District" Insights view (Tableau REST API).
2. Pull B — every signup from the NationBuilder V2 API (OAuth 2.0, parallel),
   rotating the refresh token back into Secrets Manager after the run.
3. Compare the two on `signup_id` and flag per-field mismatches.
4. Write the reports to `/tmp`, upload them to S3 (date-stamped), and email the
   mismatches report (plus the full comparison CSV) as attachments via SES.

Entry point: `lambda_pipeline.handler`.

Packaging options:

- **Primary: zip + AWS-managed pandas layer** (no Docker). Covered below.
- **Fallback: container image** (see the [appendix](#appendix-container-image)).

> This project uses its **own** S3 bucket and its **own** Secrets Manager secret,
> separate from the sibling ZipStateFed deployment. The SES sender and the
> recipient list are the same.
>
> Substitute your own `ACCOUNT_ID`, `YOUR_BUCKET`, and region as needed. The
> examples use region `eu-west-2`.

---

## 1. Prerequisites

- AWS CLI configured with credentials that can create the function, role, and
  policies (`aws sts get-caller-identity` should work).
- Python 3.12 + pip locally (to build the deps zip).
- An S3 bucket for this project's outputs + the recipient list.
- **Both** sets of credentials:
  - NationBuilder **V2 OAuth** — client id, client secret, redirect uri, and a
    refresh token from the one-time authorize handshake (see
    [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md)).
  - NationBuilder **Insights (Tableau)** — token name, token secret, site name,
    site url.

There is **no ZIP reference workbook** for this pipeline (that was the legacy
`pull_zip_state_federal.py` validator). The comparison only reads the two live
APIs.

---

## 2. Create the S3 bucket and upload the recipient list

```bash
aws s3 mb s3://YOUR_BUCKET --region eu-west-2
aws s3 cp recipients.txt s3://YOUR_BUCKET/config/recipients.txt
```

`recipients.txt` is one address per line (`#` comments). Edit the S3 object to
change who gets emailed — no redeploy needed.

---

## 3. Store the credentials in Secrets Manager (one combined secret)

Both credential sets live in a **single** JSON secret. The keys are exactly the
`NATIONBUILDER_*` (V2) and `NB_INSIGHTS_*` (Tableau) variable names the code
reads. The function **reads** these at startup and **writes back** the rotated
V2 refresh token after each run (so write access is required — see the IAM
policy).

```bash
aws secretsmanager create-secret \
  --name nb/insightslag/prod \
  --region eu-west-2 \
  --secret-string '{
    "NATIONBUILDER_SLUG":"democratsabroad",
    "NATIONBUILDER_CLIENT_ID":"your-oauth-client-id",
    "NATIONBUILDER_CLIENT_SECRET":"your-oauth-client-secret",
    "NATIONBUILDER_REDIRECT_URI":"https://localhost:8080/callback",
    "NATIONBUILDER_REFRESH_TOKEN":"your-refresh-token",
    "NB_INSIGHTS_SERVER":"https://login.insights.nationbuilder.com",
    "NB_INSIGHTS_API_VERSION":"3.23",
    "NB_INSIGHTS_SITE_NAME":"democratsabroad",
    "NB_INSIGHTS_SITE_URL":"democratsabroad",
    "NB_INSIGHTS_TOKEN_NAME":"your-insights-token-name",
    "NB_INSIGHTS_TOKEN_SECRET":"your-insights-token-secret"
  }'
```

The function env (step 7) points **both** `NATIONBUILDER_SECRET_ID` and
`NB_INSIGHTS_SECRET_ID` at this one secret. Each loader copies only the keys it
recognizes, so the combined secret serves both.

> **Why write access matters:** V2 access tokens expire after 24h, so the client
> refreshes on each run. NationBuilder rotates the refresh token on refresh, and
> the function persists the new one back into this secret. Without that, a
> scheduled run would eventually fail with a stale token. (Store the secret as
> plain JSON with no UTF-8 BOM.)

---

## 4. Create the IAM execution role

The function needs to: write CloudWatch logs, read **and write** the secret
(for refresh-token rotation), read/write the S3 outputs, and send email.

```bash
aws iam create-role --role-name insightslagcheck-lambda-role \
  --assume-role-policy-document file://aws/trust-policy.json

aws iam put-role-policy --role-name insightslagcheck-lambda-role \
  --policy-name insightslagcheck-permissions \
  --policy-document file://aws/permissions-policy.json
```

`aws/permissions-policy.json` grants logs, `secretsmanager:GetSecretValue` +
`PutSecretValue` on `nb/insightslag/prod-*`, `s3:GetObject`/`PutObject` on your
bucket, and `ses:SendEmail`/`SendRawEmail` scoped to the from-address. Replace
`ACCOUNT_ID` and `YOUR_BUCKET` first.

---

## 5. Build the deployment zip

`build_lambda_zip.py` installs the non-pandas dependencies as **Linux wheels**
and zips them with the application source (`lambda_pipeline.py`, the three
pull/compare scripts, `nb_insights.py`, and the `nationbuilder_v2` package).
pandas/numpy are excluded — they come from the AWS-managed pandas layer.

```bash
python build_lambda_zip.py        # -> lambda_deploy.zip (~3 MB)
```

> Unlike ZipStateFed, this build bundles `tableau-api-lib` and its chain
> (requests, requests-toolbelt, bleach, typeguard, packaging) because the
> Insights pull needs it.

---

## 6. Find the managed pandas layer ARN

AWS SDK for pandas ships pandas + numpy as a managed layer (AWS account
`336392948345`). For eu-west-2 / Python 3.12 / x86_64:

```
arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:<version>
```

Pick the current `<version>` from the Lambda console (Layers → AWS layers) or the
[AWS SDK for pandas layers docs](https://aws-sdk-pandas.readthedocs.io/en/stable/layers.html).

---

## 7. Create the function

```bash
aws lambda create-function \
  --function-name insightslagcheck-comparison \
  --runtime python3.12 \
  --role arn:aws:iam::ACCOUNT_ID:role/insightslagcheck-lambda-role \
  --handler lambda_pipeline.handler \
  --zip-file fileb://lambda_deploy.zip \
  --layers arn:aws:lambda:eu-west-2:336392948345:layer:AWSSDKPandas-Python312:31 \
  --timeout 900 \
  --memory-size 3008 \
  --region eu-west-2 \
  --environment file://aws/lambda-env.json
```

`aws/lambda-env.json` holds the non-secret config (replace `YOUR_BUCKET`):

```json
{
  "Variables": {
    "OUTPUT_DIR": "/tmp",
    "S3_BUCKET": "YOUR_BUCKET",
    "S3_PREFIX": "insights-lag-reports/",
    "S3_REGION": "eu-west-2",
    "SES_SENDER": "insights-reports@lategothikdata.com",
    "SES_REGION": "eu-west-2",
    "RECIPIENTS_S3_URI": "s3://YOUR_BUCKET/config/recipients.txt",
    "NATIONBUILDER_SECRET_ID": "nb/insightslag/prod",
    "NATIONBUILDER_SECRET_REGION": "eu-west-2",
    "NB_INSIGHTS_SECRET_ID": "nb/insightslag/prod",
    "NB_INSIGHTS_SECRET_REGION": "eu-west-2"
  }
}
```

Sizing notes:

- **Memory 3008 MB**: the run holds **two** full ~180k-row pulls in memory (the
  Insights DataFrame and the NationBuilder DataFrame) plus the merged
  comparison. 3008 MB runs comfortably and gives ~2 vCPU (so the parallel V2
  pull is faster too). Lower memory risks `Runtime.OutOfMemory`.
- **Timeout 900 s** (the max): the Insights pull plus the parallel V2 pull
  (~4–5 min) fit well inside 900 s.
- **/tmp**: the four report files total well under the 512 MB `/tmp` default.

To ship code updates later:

```bash
python build_lambda_zip.py
aws lambda update-function-code \
  --function-name insightslagcheck-comparison \
  --zip-file fileb://lambda_deploy.zip --region eu-west-2
```

---

## 8. Test the function

```bash
aws lambda invoke --function-name insightslagcheck-comparison \
  --payload '{}' --cli-read-timeout 900 --region eu-west-2 response.json
cat response.json
```

A success looks like:

```json
{"statusCode": 200, "result": {"status": "OK", "compared": 179701,
 "mismatches": 9, "field_mismatches": {"registered_state": 7,
 "registered_zip": 8, "federal_district": 0},
 "emailed_to": ["rick_meier@msn.com"]}}
```

Errors return `statusCode: 500` with an `error` field; the full traceback is in
CloudWatch Logs.

---

## 9. Email the report attachments (Amazon SES)

The run emails the comparison reports as attachments when `SES_SENDER` is set and
a recipient list exists. One-time SES setup:

- **Verify a sender identity.** A verified domain (DKIM) gives the best
  deliverability. The sender (`SES_SENDER`) must be on the verified domain —
  here `insights-reports@lategothikdata.com` (same as ZipStateFed).
- **Sandbox vs. production.** New SES accounts are in the sandbox and can only
  send to *verified* recipients; verify each recipient, or request production
  access. Check with
  `aws sesv2 get-account --region eu-west-2 --query ProductionAccessEnabled`.
- **Recipient list** is a plain-text file in S3 (one address per line):
  `s3://YOUR_BUCKET/config/recipients.txt`.

The IAM policy already grants `ses:SendEmail`/`ses:SendRawEmail` scoped to the
from-address (`SendRawEmail` carries the attachments). `EMAIL_MESSAGE` overrides
the default body note.

---

## 10. Schedule it (optional)

Run nightly with EventBridge Scheduler:

```bash
aws iam create-role --role-name insightslagcheck-scheduler-role \
  --assume-role-policy-document file://aws/scheduler-trust-policy.json
aws iam put-role-policy --role-name insightslagcheck-scheduler-role \
  --policy-name insightslagcheck-scheduler-invoke \
  --policy-document file://aws/scheduler-permissions-policy.json

aws scheduler create-schedule \
  --name insightslagcheck-nightly \
  --schedule-expression "cron(0 6 * * ? *)" \
  --schedule-expression-timezone "UTC" \
  --flexible-time-window '{"Mode":"OFF"}' \
  --target '{
    "Arn":"arn:aws:lambda:eu-west-2:ACCOUNT_ID:function:insightslagcheck-comparison",
    "RoleArn":"arn:aws:iam::ACCOUNT_ID:role/insightslagcheck-scheduler-role"
  }' \
  --region eu-west-2
```

---

## Appendix: container image

If you prefer a container image, the included `Dockerfile` installs pandas from
`requirements.txt` (no managed layer) and sets `CMD ["lambda_pipeline.handler"]`.
Build, push to ECR, and create the function with `--package-type Image`.
Everything else (IAM role, the `nb/insightslag/prod` secret, env vars,
scheduling) is the same as the zip path above.

```bash
aws ecr create-repository --repository-name insightslagcheck-comparison --region eu-west-2
# build + push the image, then:
aws lambda create-function --function-name insightslagcheck-comparison \
  --package-type Image \
  --code ImageUri=ACCOUNT_ID.dkr.ecr.eu-west-2.amazonaws.com/insightslagcheck-comparison:latest \
  --role arn:aws:iam::ACCOUNT_ID:role/insightslagcheck-lambda-role \
  --timeout 900 --memory-size 3008 --region eu-west-2 \
  --environment file://aws/lambda-env.json
```
