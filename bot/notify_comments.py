"""Уведомления на почту о новых комментариях на сайте La Butaca.

Запускается GitHub Actions вместе с ботом (каждые ~5–15 минут).
Берёт из Supabase комментарии (kind=comment), появившиеся с начала прошлого успешного запуска,
и шлёт одно письмо со всеми новыми комментариями.
Секреты (Settings → Secrets → Actions): SB_URL, SB_KEY, MAIL_USER (адрес Gmail, с которого слать),
MAIL_APP_PASSWORD (пароль приложения Google), MAIL_TO (куда слать; по умолчанию = MAIL_USER).
Без почтовых секретов — тихо пропускает.
"""
import html
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from email.utils import formataddr

SB_URL = os.environ.get("SB_URL", "").rstrip("/")
SB_KEY = os.environ.get("SB_KEY", "")
USER = os.environ.get("MAIL_USER", "").strip()
PASS = os.environ.get("MAIL_APP_PASSWORD", "").replace(" ", "")
TO = os.environ.get("MAIL_TO", "").strip() or USER
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "")
SITE = "https://labutaca-cine.github.io/la-butaca/"


def gh(path):
    req = urllib.request.Request("https://api.github.com" + path,
                                 headers={"Authorization": "Bearer " + GH_TOKEN, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def window():
    """Интервал [начало прошлого успешного запуска, начало этого запуска) — запуски стыкуются без дыр и повторов."""
    me = gh(f"/repos/{REPO}/actions/runs/{RUN_ID}")
    runs = gh(f"/repos/{REPO}/actions/workflows/{me['workflow_id']}/runs?status=success&per_page=1")["workflow_runs"]
    since = runs[0]["run_started_at"] if runs else me["run_started_at"]
    return since, me["run_started_at"]


def comments(since, until):
    q = urllib.parse.urlencode([("select", "*"), ("kind", "eq.comment"),
                                ("updated_at", "gte." + since), ("updated_at", "lt." + until),
                                ("order", "updated_at.asc")])
    req = urllib.request.Request(f"{SB_URL}/rest/v1/marks?{q}", headers={"apikey": SB_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return [row for row in json.load(r) if not (row.get("data") or {}).get("_deleted") and (row.get("data") or {}).get("text")]


def send(rows):
    items = []
    for row in rows:
        d = row["data"]
        items.append(f"<p><b>{html.escape(d.get('title') or row['key'])}</b><br>"
                     f"{html.escape(d.get('author') or 'кто-то')}: {html.escape(d['text'])}</p>")
    body = ("<p>💬 Новые комментарии на La Butaca:</p>" + "".join(items) +
            f'<p><a href="{SITE}">Открыть La Butaca</a></p>')
    n = len(rows)
    msg = MIMEText(body, "html", "utf-8")
    msg["Subject"] = f"La Butaca: новый комментарий ({n})" if n > 1 else \
        f"La Butaca: комментарий к «{rows[0]['data'].get('title') or rows[0]['key']}»"
    msg["From"] = formataddr(("La Butaca", USER))
    msg["To"] = TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(USER, PASS)
        s.sendmail(USER, [a.strip() for a in TO.split(",")], msg.as_string())


def main():
    since, until = window()
    rows = comments(since, until)
    print(f"новых комментариев с {since}: {len(rows)}")
    if rows:
        send(rows)
        print("письмо отправлено")


if __name__ == "__main__":
    if not all((SB_URL, SB_KEY, USER, PASS, GH_TOKEN, REPO, RUN_ID)):
        print("Почтовые секреты не добавлены (MAIL_USER, MAIL_APP_PASSWORD) — уведомления пропускаю")
    else:
        main()
