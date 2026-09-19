#!/usr/bin/env python3
"""
Dubai Weather Email Automation
================================

A single-file Python application that:
  1. Fetches current + hourly weather data for Dubai from Open-Meteo.
  2. Analyzes the weather data.
  3. Generates a professional, mobile-friendly HTML report (with plain-text fallback).
  4. Sends the report via SMTP (STARTTLS).

Run locally:
    python weather_email.py

Configuration is read entirely from environment variables (optionally loaded
from a local .env file via python-dotenv). See .env.example for the full list.

This file intentionally contains ALL application logic (config, HTTP fetching,
retry handling, analysis, HTML/text generation, SMTP delivery, logging, and
the main entry point) -- no other application source modules are used.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

try:
    # python-dotenv is only needed for local development. In GitHub Actions
    # the environment variables are provided directly via Secrets, so a
    # missing .env file (or missing package) must never break execution.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is in requirements.txt
    pass


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dubai_weather_email")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DUBAI_LATITUDE = 25.0772
DUBAI_LONGITUDE = 55.3093
DUBAI_TIMEZONE = "Asia/Dubai"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT_SECONDS = 15
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2

REQUIRED_ENV_VARS = [
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "EMAIL_TO",
]


class ConfigError(Exception):
    """Raised when required configuration/environment variables are missing or invalid."""


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    email_to: str


def load_config() -> SmtpConfig:
    """Load and validate SMTP configuration from environment variables.

    Never logs the password or any other secret value.
    """
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}"
        )

    raw_port = os.environ["SMTP_PORT"]
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ConfigError(f"SMTP_PORT must be an integer, got: {raw_port!r}") from exc

    return SmtpConfig(
        host=os.environ["SMTP_HOST"],
        port=port,
        username=os.environ["SMTP_USERNAME"],
        password=os.environ["SMTP_PASSWORD"],
        email_to=os.environ["EMAIL_TO"],
    )


# --------------------------------------------------------------------------
# Weather code -> human readable condition
# --------------------------------------------------------------------------

WEATHER_CODE_MAP = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def describe_weather_code(code: Any) -> str:
    """Convert an Open-Meteo weather code into a readable condition string.

    Handles unknown/missing codes safely by returning a fallback description
    rather than raising an exception.
    """
    try:
        code_int = int(code)
    except (TypeError, ValueError):
        return "Unknown conditions"
    return WEATHER_CODE_MAP.get(code_int, f"Unknown conditions (code {code_int})")


# --------------------------------------------------------------------------
# Open-Meteo fetching with retry handling
# --------------------------------------------------------------------------

class WeatherFetchError(Exception):
    """Raised when weather data cannot be retrieved or parsed."""


def fetch_weather_data() -> dict:
    """Fetch current + hourly weather data for Dubai from Open-Meteo.

    Retries on network errors and 5xx/429 responses with exponential backoff.
    Raises WeatherFetchError if all attempts fail or the response is invalid.
    """
    params = {
        "latitude": DUBAI_LATITUDE,
        "longitude": DUBAI_LONGITUDE,
        "timezone": DUBAI_TIMEZONE,
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "precipitation",
                "rain",
                "weather_code",
                "cloud_cover",
            ]
        ),
        "hourly": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "dew_point_2m",
                "apparent_temperature",
                "precipitation_probability",
                "rain",
                "precipitation",
            ]
        ),
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Fetching Open-Meteo weather data (attempt %d/%d)", attempt, MAX_RETRIES)
            response = requests.get(OPEN_METEO_URL, params=params, timeout=HTTP_TIMEOUT_SECONDS)

            if response.status_code == 429 or response.status_code >= 500:
                raise WeatherFetchError(
                    f"Open-Meteo returned a retryable HTTP status: {response.status_code}"
                )

            response.raise_for_status()

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                raise WeatherFetchError(f"Open-Meteo response was not valid JSON: {exc}") from exc

            validate_weather_payload(data)
            logger.info("Weather data received")
            return data

        except (requests.RequestException, WeatherFetchError) as exc:
            last_error = exc
            logger.warning("Weather fetch attempt %d failed: %s", attempt, exc)
            if attempt < MAX_RETRIES:
                sleep_seconds = RETRY_BACKOFF_SECONDS * attempt
                time.sleep(sleep_seconds)

    raise WeatherFetchError(f"Failed to fetch weather data after {MAX_RETRIES} attempts: {last_error}")


def validate_weather_payload(data: dict) -> None:
    """Ensure the Open-Meteo response contains the fields this app depends on."""
    if not isinstance(data, dict):
        raise WeatherFetchError("Weather payload is not a JSON object")

    if "current" not in data or "hourly" not in data:
        raise WeatherFetchError("Weather payload is missing 'current' or 'hourly' section")

    required_current = [
        "temperature_2m",
        "relative_humidity_2m",
        "precipitation",
        "rain",
        "weather_code",
        "cloud_cover",
    ]
    missing_current = [f for f in required_current if f not in data["current"]]
    if missing_current:
        raise WeatherFetchError(f"Missing current weather field(s): {missing_current}")

    required_hourly = [
        "time",
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "apparent_temperature",
        "precipitation_probability",
        "rain",
        "precipitation",
    ]
    missing_hourly = [f for f in required_hourly if f not in data["hourly"]]
    if missing_hourly:
        raise WeatherFetchError(f"Missing hourly weather field(s): {missing_hourly}")


# --------------------------------------------------------------------------
# Weather analysis
# --------------------------------------------------------------------------

@dataclass
class WeatherAnalysis:
    current_temperature: float
    current_humidity: float
    current_precipitation: float
    current_rain: float
    current_cloud_cover: float
    current_condition: str

    min_temperature: float
    max_temperature: float
    max_apparent_temperature: float
    average_humidity: float
    max_precipitation_probability: float
    expected_total_rainfall: float
    max_hourly_rainfall: float
    rain_risk_hours: list = field(default_factory=list)

    summary: str = ""


def analyze_weather(data: dict) -> WeatherAnalysis:
    """Compute derived weather statistics from the raw Open-Meteo payload."""
    current = data["current"]
    hourly = data["hourly"]

    temps = [t for t in hourly["temperature_2m"] if t is not None]
    apparent_temps = [t for t in hourly["apparent_temperature"] if t is not None]
    humidities = [h for h in hourly["relative_humidity_2m"] if h is not None]
    precip_probs = [p for p in hourly["precipitation_probability"] if p is not None]
    rains = [r if r is not None else 0.0 for r in hourly["rain"]]
    times = hourly["time"]

    if not temps or not humidities or not precip_probs:
        raise WeatherFetchError("Hourly weather data is incomplete; cannot analyze")

    rain_risk_hours = [
        times[i]
        for i, prob in enumerate(precip_probs)
        if prob is not None and prob >= 50
    ]

    analysis = WeatherAnalysis(
        current_temperature=current["temperature_2m"],
        current_humidity=current["relative_humidity_2m"],
        current_precipitation=current["precipitation"],
        current_rain=current["rain"],
        current_cloud_cover=current["cloud_cover"],
        current_condition=describe_weather_code(current["weather_code"]),
        min_temperature=min(temps),
        max_temperature=max(temps),
        max_apparent_temperature=max(apparent_temps) if apparent_temps else max(temps),
        average_humidity=round(sum(humidities) / len(humidities), 1),
        max_precipitation_probability=max(precip_probs),
        expected_total_rainfall=round(sum(rains), 1),
        max_hourly_rainfall=round(max(rains), 1) if rains else 0.0,
        rain_risk_hours=rain_risk_hours,
    )

    analysis.summary = generate_summary(analysis)
    return analysis


def generate_summary(a: WeatherAnalysis) -> str:
    """Generate a concise, human-readable weather summary from analyzed data only."""
    if a.max_temperature >= 38:
        temp_feel = "a hot day"
    elif a.max_temperature >= 32:
        temp_feel = "a warm day"
    else:
        temp_feel = "a mild day"

    if a.average_humidity >= 70:
        humidity_feel = "high"
    elif a.average_humidity >= 40:
        humidity_feel = "moderate"
    else:
        humidity_feel = "low"

    if a.max_precipitation_probability >= 60:
        rain_feel = "a notable chance of rain"
    elif a.max_precipitation_probability >= 30:
        rain_feel = "a slight chance of rain"
    else:
        rain_feel = "low rainfall probability"

    summary = (
        f"Dubai will experience {temp_feel} with temperatures ranging from "
        f"{round(a.min_temperature)}°C to {round(a.max_temperature)}°C. "
        f"Humidity will remain {humidity_feel} (avg {a.average_humidity}%), "
        f"while the forecast shows {rain_feel} "
        f"(up to {round(a.max_precipitation_probability)}%)."
    )
    return summary


# --------------------------------------------------------------------------
# Email content generation (HTML + plain text)
# --------------------------------------------------------------------------

def format_rain_risk_hours(hours: list, limit: int = 6) -> str:
    if not hours:
        return "None expected"
    formatted = []
    for h in hours[:limit]:
        try:
            dt = datetime.fromisoformat(h)
            formatted.append(dt.strftime("%I:%M %p").lstrip("0"))
        except ValueError:
            formatted.append(h)
    text = ", ".join(formatted)
    if len(hours) > limit:
        text += f" (+{len(hours) - limit} more)"
    return text


def generate_html_email(analysis: WeatherAnalysis, report_date: str, generated_at: str) -> str:
    rain_risk_text = format_rain_risk_hours(analysis.rain_risk_hours)

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dubai Weather Report</title>
</head>
<body style="margin:0;padding:0;background-color:#f2f4f7;font-family:Arial,Helvetica,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f2f4f7;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width:600px;background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
          <tr>
            <td style="background-color:#0f5ea8;padding:24px 32px;">
              <h1 style="margin:0;color:#ffffff;font-size:22px;">Dubai Weather</h1>
              <p style="margin:4px 0 0;color:#cfe3f7;font-size:14px;">Daily Weather Report &middot; {report_date}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px;">
              <table role="presentation" width="100%" style="background-color:#f7fafd;border-radius:6px;padding:16px;">
                <tr>
                  <td style="padding:8px;">
                    <p style="margin:0;font-size:36px;font-weight:bold;color:#0f5ea8;">{analysis.current_temperature}&deg;C</p>
                    <p style="margin:4px 0 0;font-size:15px;color:#333333;">{analysis.current_condition}</p>
                  </td>
                  <td style="padding:8px;text-align:right;font-size:13px;color:#555555;">
                    <p style="margin:0;">Humidity: {analysis.current_humidity}%</p>
                    <p style="margin:4px 0 0;">Rain: {analysis.current_rain} mm</p>
                    <p style="margin:4px 0 0;">Cloud cover: {analysis.current_cloud_cover}%</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px;">
              <h2 style="font-size:16px;color:#0f5ea8;margin:0 0 12px;border-bottom:1px solid #e5e9ef;padding-bottom:8px;">Today's Outlook</h2>
              <table role="presentation" width="100%" style="font-size:14px;color:#333333;border-collapse:collapse;">
                <tr>
                  <td style="padding:6px 0;color:#666666;">Minimum temperature</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{round(analysis.min_temperature)}&deg;C</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Maximum temperature</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{round(analysis.max_temperature)}&deg;C</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Maximum feels-like temperature</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{round(analysis.max_apparent_temperature)}&deg;C</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Average humidity</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{analysis.average_humidity}%</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Rain probability</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{round(analysis.max_precipitation_probability)}%</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Expected rainfall</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{analysis.expected_total_rainfall} mm</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Maximum hourly rainfall</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{analysis.max_hourly_rainfall} mm</td>
                </tr>
                <tr>
                  <td style="padding:6px 0;color:#666666;">Rain-risk hours</td>
                  <td style="padding:6px 0;text-align:right;font-weight:bold;">{rain_risk_text}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:8px 32px 28px;">
              <h2 style="font-size:16px;color:#0f5ea8;margin:0 0 12px;border-bottom:1px solid #e5e9ef;padding-bottom:8px;">Weather Analysis</h2>
              <p style="font-size:14px;line-height:1.5;color:#333333;margin:0;">{analysis.summary}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 32px;background-color:#f7fafd;border-top:1px solid #e5e9ef;">
              <p style="margin:0;font-size:12px;color:#8a94a3;">Generated on {generated_at} (Asia/Dubai)</p>
              <p style="margin:4px 0 0;font-size:12px;color:#8a94a3;">Source: Open-Meteo &middot; Dubai Weather Email Automation</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return html


def generate_plain_text_email(analysis: WeatherAnalysis, report_date: str, generated_at: str) -> str:
    rain_risk_text = format_rain_risk_hours(analysis.rain_risk_hours)

    text = f"""\
DUBAI WEATHER REPORT - {report_date}
=====================================

Current Conditions
-------------------
Temperature: {analysis.current_temperature} C
Condition: {analysis.current_condition}
Humidity: {analysis.current_humidity}%
Rain: {analysis.current_rain} mm
Cloud cover: {analysis.current_cloud_cover}%

Today's Outlook
-------------------
Minimum temperature: {round(analysis.min_temperature)} C
Maximum temperature: {round(analysis.max_temperature)} C
Maximum feels-like temperature: {round(analysis.max_apparent_temperature)} C
Average humidity: {analysis.average_humidity}%
Rain probability: {round(analysis.max_precipitation_probability)}%
Expected rainfall: {analysis.expected_total_rainfall} mm
Maximum hourly rainfall: {analysis.max_hourly_rainfall} mm
Rain-risk hours: {rain_risk_text}

Weather Analysis
-------------------
{analysis.summary}

Generated on {generated_at} (Asia/Dubai)
Source: Open-Meteo - Dubai Weather Email Automation
"""
    return text


# --------------------------------------------------------------------------
# Email sending (SMTP)
# --------------------------------------------------------------------------

class EmailSendError(Exception):
    """Raised when the email cannot be composed or sent."""


def send_email(config: SmtpConfig, subject: str, html_body: str, text_body: str) -> None:
    """Compose and send the weather report email via SMTP with STARTTLS."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.username
    message["To"] = config.email_to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context()
        logger.info("Connecting to SMTP server %s:%d", config.host, config.port)
        with smtplib.SMTP(config.host, config.port, timeout=HTTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            try:
                server.login(config.username, config.password)
            except smtplib.SMTPAuthenticationError as exc:
                raise EmailSendError("SMTP authentication failed. Check SMTP_USERNAME/SMTP_PASSWORD.") from exc
            server.send_message(message)
    except smtplib.SMTPConnectError as exc:
        raise EmailSendError(f"Could not connect to SMTP server {config.host}:{config.port}") from exc
    except smtplib.SMTPException as exc:
        raise EmailSendError(f"SMTP error while sending email: {exc}") from exc
    except OSError as exc:
        raise EmailSendError(f"Network error while sending email: {exc}") from exc


# --------------------------------------------------------------------------
# Main execution
# --------------------------------------------------------------------------

def main() -> int:
    logger.info("Starting Dubai Weather Automation")

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    try:
        raw_data = fetch_weather_data()
    except WeatherFetchError as exc:
        logger.error("Failed to fetch weather data: %s", exc)
        return 1

    try:
        logger.info("Analyzing weather")
        analysis = analyze_weather(raw_data)
    except (WeatherFetchError, KeyError, ValueError, ZeroDivisionError) as exc:
        logger.error("Failed to analyze weather data: %s", exc)
        return 1

    dubai_now = datetime.now(ZoneInfo(DUBAI_TIMEZONE))
    report_date = dubai_now.strftime("%A, %d %B %Y")
    generated_at = dubai_now.strftime("%Y-%m-%d %H:%M:%S")

    logger.info("Generating email")
    subject = "Dubai Weather Report - Daily Update"
    html_body = generate_html_email(analysis, report_date, generated_at)
    text_body = generate_plain_text_email(analysis, report_date, generated_at)

    try:
        logger.info("Sending email")
        send_email(config, subject, html_body, text_body)
    except EmailSendError as exc:
        logger.error("Failed to send email: %s", exc)
        return 1

    logger.info("Email sent successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
