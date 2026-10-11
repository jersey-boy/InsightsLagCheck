# Deploying the comparison pipeline

The **Insights vs NationBuilder comparison** runs as an AWS Lambda
(`lambda_pipeline.handler`): it pulls both systems, compares every `signup_id`
present in both across five fields, writes the reports to `/tmp`, uploads them to
S3 (date-stamped), and emails the mismatches report via SES. A nightly
EventBridge schedule runs it at 06:00 UTC.

Deployment is **infrastructure-as-code** via **Serverless Framework v3**
(`serverless.yml`), driven by a **GitHub Actions** workflow on every push to
`main`. CloudFormation owns the function, its IAM execution role, the log group,
and the schedule. Two resources are **external** (not managed by the stack
because they hold live state): the S3 reports bucket and the Secrets Manager
secret.

Reference values below are the live deployment (account `068238656047`, region
`eu-west-2`, bucket `insightslagcheck-reports-068238656047`, secret
`nb/insightslag/prod`). Substitute your own as needed.

---

## Architecture

```
GitHub push to main ─▶ GitHub Actions (.github/workflows/deploy.yml)
                         │  build_lambda_zip.py  → lambda_deploy.zip
                         │  npx serverless deploy
                         ▼
                   CloudFormation stack: insightslagcheck-prod
                     ├─ Lambda  insightslagcheck-comparison
                     ├─ IAM execution role
                     ├─ Log group
                     └─ EventBridge schedule (cron 0 6 * * ? * UTC)
                         │ invokes
                         ▼
            Insights (Tableau) + NationBuilder V2  ──▶  compare
                         │ writes /tmp, uploads to S3, emails via SES
                         ▼
   S3 insightslagcheck-reports-...   Secrets Manager nb/insightslag/prod
   (external, not CFN-managed)       (external, not CFN-managed)
```

---

## One-time prerequisites (external to the stack)

These are created once, outside Serverless, because they outlive any single
deploy and hold state.

### 1. S3 reports bucket + recipient list

```bash
aws s3 mb s3://YOUR_BUCKET --region eu-west-2
aws s3 cp recipients.txt s3://YOUR_BUCKET/config/recipients.txt
```

`recipients.txt` is one address per line (`#` comments). Edit the S3 object to
change who gets emailed — no redeploy needed.

### 2. Combined Secrets Manager secret

Both credential sets live in a **single** JSON secret whose keys are exactly the
`NATIONBUILDER_*` (V2 OAuth) and `NB_INSIGHTS_*` (Tableau) variable names the
code reads. The function **reads** these at startup and **writes back** the
rotated V2 refresh token after each run (so the role has `PutSecretValue`).

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

> **Why write access matters:** V2 access tokens expire after 24h, so the client
> refreshes on each run. NationBuilder rotates the refresh token on refresh, and
> the function persists the new one back into this secret. Store it as plain
> JSON with no UTF-8 BOM. (Getting the first refresh token is a one-time browser
> step — see [`V2_OAUTH_SETUP.md`](V2_OAUTH_SETUP.md).)

Everything else (function, role, log group, schedule) is created by
`serverless deploy`.

---

## GitHub Actions (the normal path)

`.github/workflows/deploy.yml` runs on every push to `main` (and via **Run
workflow** in the Actions tab). It sets up Python 3.12 + Node 20, builds
`lambda_deploy.zip` with `build_lambda_zip.py`, installs the pinned Serverless
CLI (`npm ci`), and runs `serverless deploy`.

### Required repository secrets

Repo **Settings** → sidebar **Security** group → **Secrets and variables** →
**Actions** → **New repository secret** (direct link:
`https://github.com/<owner>/<repo>/settings/secrets/actions`):

| Secret | What it is |
|--------|------------|
| `AWS_ACCESS_KEY_ID` | IAM user key allowed to deploy the CloudFormation stack. |
| `AWS_SECRET_ACCESS_KEY` | that user's secret. |
| `SERVERLESS_ACCESS_KEY` | Serverless dashboard access key (v3 login). |

`AWS_REGION` is set to `eu-west-2` in the workflow. Push to `main` (or trigger
the workflow manually) and it deploys.

> **Where these keys live:** nowhere in the repo. GitHub Actions secrets are
> stored encrypted in GitHub (Settings → Security → Secrets and variables →
> Actions) and are only *referenced* by name in the workflow. The app's own runtime
> credentials are separate: in `.env` for local runs (gitignored) and in the
> `nb/insightslag/prod` Secrets Manager secret for the Lambda.

> **TODO — clean up the CI credentials (shortcut taken for speed).** The three
> repo secrets currently reuse *existing* keys rather than dedicated ones:
> `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are the existing admin user's keys,
> and `SERVERLESS_ACCESS_KEY` is the pre-existing dashboard key (which was also
> exposed in plaintext during setup). Before relying on CI long-term: create a
> dedicated least-privilege deploy IAM user, generate fresh AWS + Serverless
> keys, update the three GitHub secrets, and deactivate/rotate the old keys.
> Tracked in `ToDoList.txt` (Tech debt / cleanup).

---

## Deploying from a laptop (manual)

Same result as CI, useful for the first deploy or debugging:

```bash
npm ci                                  # install pinned serverless@3
python build_lambda_zip.py              # -> lambda_deploy.zip
# AWS creds via your profile/env; plus:
$env:SERVERLESS_ACCESS_KEY="..."        # PowerShell;  export on bash
npx serverless deploy --stage prod --region eu-west-2
```

`serverless.yml` wires up: handler `lambda_pipeline.handler`, python3.12, 3008 MB
memory / 900 s timeout, the AWS-managed pandas layer, the IAM role statements
(secret read/write, S3 read/write, SES send, logs), the env vars, and the
nightly schedule. It **references** the external bucket and secret by name.

> **Packaging:** we ship a pre-built artifact (`package.artifact:
> lambda_deploy.zip`) from `build_lambda_zip.py`, which installs the non-pandas
> deps — including `tableau-api-lib` — as Linux wheels (pandas/numpy come from
> the managed layer). Always run the build before `serverless deploy`
> (`npm run deploy` does both).

### Test the deployed function

```bash
aws lambda invoke --function-name insightslagcheck-comparison \
  --payload '{}' --cli-read-timeout 900 --region eu-west-2 response.json
cat response.json
```

Success looks like:

```json
{"statusCode": 200, "result": {"status": "OK", "compared": 189088,
 "mismatches": 10, "field_mismatches": {"registered_state": 2,
 "registered_zip": 7, "federal_district": 0, "membership_country": 3,
 "us_citizen": 0}, "emailed_to": ["rick_meier@msn.com"]}}
```

Errors return `statusCode: 500` with an `error` field; the full traceback is in
CloudWatch Logs (`/aws/lambda/insightslagcheck-comparison`).

---

## Sizing

- **Memory 3008 MB**: the run holds **two** full ~189k-row pulls in memory plus
  the merged comparison; lower memory risks `Runtime.OutOfMemory`. 3008 MB also
  gives ~2 vCPU, so the parallel V2 pull is faster.
- **Timeout 900 s** (the max): the Insights pull + parallel V2 pull (~4–5 min)
  fit comfortably.

---

## SES notes

- **Verify a sender identity.** `SES_SENDER` (`insights-reports@lategothikdata.com`)
  must be on a verified domain (DKIM recommended).
- **Sandbox vs. production.** A sandbox account can only send to *verified*
  recipients. Check:
  `aws sesv2 get-account --region eu-west-2 --query ProductionAccessEnabled`.
  The current recipient `rick_meier@msn.com` is a verified identity, so sending
  works in the sandbox.

---

## Troubleshooting

**`AWS::EarlyValidation::ResourceExistenceCheck` fails on deploy / "Validation
failed with 1 error(s)".** The account has an early-validation guardrail that
rejects a change set which tries to **create a resource that already exists** —
most commonly the CloudWatch log group `/aws/lambda/insightslagcheck-comparison`
left behind by a previously hand-created function. Delete the orphan and
redeploy:

```bash
aws logs delete-log-group \
  --log-group-name /aws/lambda/insightslagcheck-comparison --region eu-west-2
```

(If a bare deploy still shows only the generic "Validation failed" message,
remove any `deploymentMethod: direct` from `serverless.yml` so Serverless uses a
change set — the change-set path surfaces the real hook error.)

**`serverless remove`** tears the whole stack down (function, role, schedule,
log group, deployment bucket). The external S3 reports bucket and the secret are
**not** removed — delete those by hand if ever needed.

---

## Future upgrades (noted, not yet done)

- **Serverless Framework v4.** This targets v3 (what the current account/key
  supports). v4 needs a licensed `SERVERLESS_ACCESS_KEY` for the CLI itself and
  has minor config changes. A side effect worth having: v4's bundled schema
  knows `python3.12`, so the cosmetic runtime-validation warning v3 prints would
  go away.
- **OIDC instead of static AWS keys.** The workflow uses static
  `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` secrets for simplicity. Prefer a
  GitHub → AWS OIDC trust role (no long-lived keys in GitHub): add
  `permissions: { id-token: write, contents: read }` to the job and swap the env
  creds for `aws-actions/configure-aws-credentials` with `role-to-assume`.
- **`serverless-python-requirements` instead of the prebuilt zip.** The plugin
  can build the Linux wheels itself (needs Docker), replacing
  `build_lambda_zip.py` + `package.artifact`.

---

## Appendix: container image (alternative packaging)

The included `Dockerfile` installs pandas from `requirements.txt` (no managed
layer) and sets `CMD ["lambda_pipeline.handler"]`. Build, push to ECR, and point
the function at the image. Not used by the Serverless path above.
