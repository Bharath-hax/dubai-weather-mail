# Dubai Weather Email Automation

A single-file Python application that fetches Dubai's current and hourly
weather from [Open-Meteo](https://open-meteo.com/), analyzes it, and emails
a professional, mobile-friendly HTML weather report every day.

All application logic lives in one file: **`weather_email.py`**.

## What it does

1. Fetches current + hourly weather data for Dubai (25.0772, 55.3093) from Open-Meteo.
2. Analyzes the data: min/max temperature, feels-like temperature, average
   humidity, rain probability, expected rainfall, and rain-risk hours.
3. Generates a plain-language weather summary based only on the retrieved data.
4. Builds a clean HTML email (with a plain-text fallback) titled
   **"Dubai Weather Report - Daily Update"**.
5. Sends it via SMTP (STARTTLS) to the address in `EMAIL_TO`.
6. Runs locally, or automatically/manually via GitHub Actions.

## Requirements

- Python 3.10
- Dependencies listed in `requirements.txt` (`requests`, `python-dotenv`)

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

## 2. Create your `.env` file

Copy the example file and fill in your own values:

```bash
cp .env.example .env
```

Edit `.env`:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_TO=splitsettleai@gmail.com
```

> If you use Gmail, `SMTP_PASSWORD` must be a 16-character
> [App Password](https://myaccount.google.com/apppasswords), not your normal
> account password (Gmail requires 2-Step Verification to be enabled first).

**Never commit `.env`.** It is already listed in `.gitignore`.

## 3. Run locally

```bash
python weather_email.py
```

On success you'll see log lines ending with `INFO - Email sent successfully`
and the report will arrive in the `EMAIL_TO` inbox.

## 4. Configure GitHub Secrets

In your GitHub repository, go to:

**Settings → Secrets and variables → Actions → New repository secret**

Add each of the following secrets:

| Secret name     | Example value              |
|------------------|-----------------------------|
| `SMTP_HOST`     | `smtp.gmail.com`            |
| `SMTP_PORT`     | `587`                       |
| `SMTP_USERNAME` | `your-email@gmail.com`      |
| `SMTP_PASSWORD` | `your-app-password`         |
| `EMAIL_TO`      | `splitsettleai@gmail.com`   |

## 5. Push to GitHub

```bash
git init
git add .
git commit -m "Dubai weather email automation"
git branch -M main
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

## 6. Open GitHub Actions

In your repository, click the **Actions** tab. You should see a workflow
named **Dubai Weather Email** listed on the left.

## 7. Run it manually ("Run workflow")

1. Go to **Actions → Dubai Weather Email**.
2. Click **Run workflow** (top right).
3. Select the branch (usually `main`) and click the green **Run workflow** button.
4. Watch the run's logs to confirm `Email sent successfully`.

## 8. Automatic daily schedule

The workflow also runs automatically every day at **7:00 AM Asia/Dubai
time**, which is **03:00 UTC** (`cron: "0 3 * * *"`), since Dubai is UTC+4
and does not observe daylight saving time.

The workflow uses a concurrency group (`dubai-weather-email`) so overlapping
runs (e.g. a manual run triggered while the scheduled run is still executing)
are prevented from running at the same time.

## Troubleshooting

- **`Configuration error: Missing required environment variable(s): ...`**
  One or more of `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`,
  `EMAIL_TO` is not set. Check your `.env` file locally, or your repository
  Secrets on GitHub.

- **`SMTP authentication failed`**
  Double-check `SMTP_USERNAME`/`SMTP_PASSWORD`. For Gmail, make sure you are
  using an App Password, not your regular account password, and that 2-Step
  Verification is enabled.

- **`Could not connect to SMTP server`**
  Verify `SMTP_HOST` and `SMTP_PORT` are correct for your email provider, and
  that outbound connections on that port are not blocked.

- **`Failed to fetch weather data after 3 attempts`**
  Open-Meteo may be temporarily unavailable, or there's a network issue. The
  app retries automatically up to 3 times with backoff before failing.

- **The scheduled workflow didn't run**
  GitHub Actions schedules can be delayed by a few minutes during high load,
  and scheduled workflows are automatically disabled after 60 days of
  repository inactivity — push a commit or run the workflow manually to
  re-enable it.

- **Workflow fails with a missing secrets error**
  Confirm all five secrets (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`,
  `SMTP_PASSWORD`, `EMAIL_TO`) are added under **Settings → Secrets and
  variables → Actions** in the repository (not just your local `.env`).

## Project structure

```
dubai-weather-mail/
├── weather_email.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── .github/
    └── workflows/
        └── workflow.yml
```
