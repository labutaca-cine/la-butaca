"""Бот La Mesa: меню на неделю для троих — пожелания, отметки и рассылка в канал.

Запускается GitHub Actions (.github/workflows/mesa-bot.yml) каждые ~10 минут.
Секреты (Settings → Secrets → Actions): MESA_TG_TOKEN — токен бота, MESA_CHANNEL — @канал или его id,
SB_URL и SB_KEY — Supabase (те же, что у La Butaca; отметки kind = "mesa").
Меню бот берёт из mesa/menu.json (его пишет build_mesa.py вместе с сайтом).

Команды (в личке и в группе обсуждения):
  /hoy — ужин сегодня · /manana — завтра · /semana — ужины недели · /compra — что ещё не куплено
  /quiero Блюдо — почему — пожелание на следующую неделю (видно на сайте в «Queremos», агент учтёт)
  /hecho — приготовили сегодняшний ужин · /rico — понравился · /nomas — больше не предлагать
В личке любой текст без команды = пожелание.
Рассылка в канал (по времени Мадрида): Сб 11:00 — меню недели, Пн–Пт 17:00 — ужин сегодня, Ср 9:00 — докупка.
"""
import datetime
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TG = os.environ.get("MESA_TG_TOKEN", "").strip()
CHANNEL = os.environ.get("MESA_CHANNEL", "").strip()
SB_URL = os.environ.get("SB_URL", "").rstrip("/")
SB_KEY = os.environ.get("SB_KEY", "")
SITE = "https://labutaca-cine.github.io/la-butaca/mesa/"
ROOT = pathlib.Path(__file__).resolve().parent.parent
MENU = json.loads((ROOT / "mesa" / "menu.json").read_text(encoding="utf-8")) if (ROOT / "mesa" / "menu.json").exists() else {}
NOW = datetime.datetime.now(ZoneInfo("Europe/Madrid"))
WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

HELP = ("🍽 Бот La Mesa — меню на неделю для нас троих.\n\n"
        "/hoy — что на ужин сегодня\n/manana — что завтра\n/semana — все ужины недели\n/compra — что ещё купить\n"
        "/quiero Блюдо — почему — хочу на следующей неделе\n"
        "/hecho — приготовили · /rico — вкусно · /nomas — больше не надо\n\n"
        f"Меню с рецептами и фото: {SITE}")


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{TG}/{method}", data=data, timeout=30) as r:
        return json.load(r)


def sb(path, body=None):
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, method="POST" if body else "GET",
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"apikey": SB_KEY, "Content-Type": "application/json",
                                          "Prefer": "resolution=merge-duplicates"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def marks():
    out = {}
    for r in sb("marks?select=key,data&kind=eq.mesa") or []:
        name, _, doc = str(r["key"]).partition("~")
        if doc and not (r.get("data") or {}).get("_deleted"):
            out.setdefault(name, {})[doc] = r["data"]
    return out


def put(coll, doc, data):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sb("marks", {"kind": "mesa", "key": f"{coll}~{doc}", "data": dict(data, at=now), "updated_at": now})


def day(offset=0):
    d = (NOW + datetime.timedelta(days=offset)).date().isoformat()
    return next((x for x in MENU.get("days", []) if x["date"] == d), None)


def dinner_text(offset=0, title="Сегодня"):
    d = day(offset)
    if not d:
        return "Меню на эти дни ещё нет — оно появится в субботу."
    m = d.get("dinner")
    if not m:
        return f"{title}: {d.get('note') or 'свобода — ничего не готовим'} 🙂"
    lines = [f"🍳 {title} ужин: {m['title']} · {m.get('time', '—')} мин"]
    if m.get("need"):
        lines.append("Нужно: " + ", ".join(m["need"]))
    if m.get("step"):
        lines.append("Шаг 1: " + m["step"])
    nxt = day(offset + 1)
    if nxt and nxt.get("lunch"):
        lines.append("Завтра на обед: " + nxt["lunch"])
    lines.append("Рецепт: " + SITE)
    return "\n".join(lines)


def week_text():
    if not MENU:
        return "Меню ещё нет."
    rows = [f"📅 Меню {MENU.get('title', '')}"]
    for d in MENU["days"]:
        dt = datetime.date.fromisoformat(d["date"])
        rows.append(f"{WD[dt.weekday()]} {dt.day}: " + (d["dinner"]["title"] if d.get("dinner") else (d.get("note") or "свобода")))
    if MENU.get("total"):
        rows.append(f"🛒 Покупки: {MENU['total']}")
    rows.append(SITE)
    return "\n".join(rows)


def shop_text(only_wed=False):
    mk = marks()
    done = set(mk.get("bought", {})) | set(mk.get("home", {}))
    left = [i for g in MENU.get("shopping", []) for i in g["items"]
            if i["key"] not in done and (not only_wed or i.get("when") == "wed")]
    if not left:
        return "🛒 Всё куплено. ¡Bien hecho!"
    head = "🛒 Докупить в среду:" if only_wed else "🛒 Ещё не куплено:"
    return "\n".join([head] + [f"• {i['name']} — {i.get('qty', '')}" for i in left[:40]] + [SITE])


def mark_today(coll):
    d = day(0)
    m = d and d.get("dinner")
    if not m:
        return "Сегодня ужина в меню нет."
    put(coll, m["key"], {"title": m["title"], "date": d["date"], "slot": "dinner", "by": "telegram"})
    return {"cooked": f"✅ Отметил: «{m['title']}» приготовили. ¡Buen provecho!",
            "liked": f"♥ Запомнил: «{m['title']}» понравился — будет чаще.",
            "nope": f"✕ Понял: «{m['title']}» больше не предлагаем."}[coll]


def handle_updates():
    updates = tg("getUpdates", timeout=0, allowed_updates='["message"]').get("result", [])
    print(f"сообщений: {len(updates)}")
    last = None
    for u in updates:
        last = u["update_id"]
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        chat = m.get("chat") or {}
        group = chat.get("type") in ("group", "supergroup")
        if not text or ((m.get("from") or {}).get("is_bot") and not m.get("sender_chat")) or m.get("is_automatic_forward"):
            continue
        reply = lambda t: tg("sendMessage", chat_id=chat["id"], text=t, reply_to_message_id=m["message_id"],
                             disable_web_page_preview="true")
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        arg = text.split(None, 1)[1] if cmd and len(text.split(None, 1)) > 1 else ("" if cmd else text)
        try:
            if cmd in ("/start", "/help"):
                reply(HELP)
            elif cmd == "/hoy":
                reply(dinner_text(0))
            elif cmd in ("/manana", "/mañana"):
                reply(dinner_text(1, "Завтра"))
            elif cmd == "/semana":
                reply(week_text())
            elif cmd == "/compra":
                reply(shop_text())
            elif cmd in ("/hecho", "/rico", "/nomas"):
                reply(mark_today({"/hecho": "cooked", "/rico": "liked", "/nomas": "nope"}[cmd]))
            elif cmd == "/quiero" or (not cmd and not group):
                first, _, rest = arg.partition("\n")
                title, _, why = first.partition("—")
                if not title.strip():
                    reply("Напишите так: /quiero Блюдо — почему")
                    continue
                who = m.get("from") or {}
                author = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or who.get("username") or "кто-то"
                uid = hashlib.sha1(f"{chat.get('id')}:{m.get('message_id')}".encode()).hexdigest()[:16]
                put("suggest", "tg-" + uid, {"title": title.strip()[:120], "author": author[:40],
                                             "note": (why or rest).strip()[:200], "source": "telegram"})
                reply(f"✅ ¡Apuntado! «{title.strip()}» — учтём в меню следующей недели. Видно на сайте в «Queremos».")
            # остальное в группе — обычная болтовня, молчим
        except Exception as e:  # одна ошибка не должна останавливать остальные сообщения
            print("ошибка:", e)
    if last is not None:
        tg("getUpdates", offset=last + 1, limit=1, timeout=0)


def broadcast():
    """Посты в канал: по одному разу в нужный час (GitHub запускает с запасом, проверяем час Мадрида)."""
    if not CHANNEL or not MENU:
        return
    wd, hour = NOW.weekday(), NOW.hour
    slot = None
    if wd == 5 and hour == 11:
        slot, text = "week", week_text()
    elif wd < 5 and hour == 17:
        slot, text = "dinner", dinner_text(0)
    elif wd == 2 and hour == 9:
        slot, text = "wed", shop_text(only_wed=True)
    if not slot:
        return
    tag = f"{slot}-{NOW.date().isoformat()}"
    if tag in marks().get("botpost", {}):
        return
    tg("sendMessage", chat_id=CHANNEL, text=text, disable_web_page_preview="true")
    put("botpost", tag, {"slot": slot})
    print("пост в канал:", tag)


if __name__ == "__main__":
    if not all((TG, SB_URL, SB_KEY)):
        print("Секреты ещё не добавлены (MESA_TG_TOKEN, SB_URL, SB_KEY) — пропускаю")
        sys.exit(0)
    # понятная причина сбоя в журнале GitHub (без секретов)
    if ":" not in TG or " " in TG:
        sys.exit("MESA_TG_TOKEN не похож на токен бота (нужен вид 123456789:AA...). Вставьте секрет заново.")
    try:
        handle_updates()
        broadcast()
    except urllib.error.HTTPError as e:
        where = "Telegram" if "telegram" in e.url else "Supabase"
        hint = " — неверный токен бота, вставьте MESA_TG_TOKEN заново" if where == "Telegram" and e.code in (401, 404) else ""
        sys.exit(f"{where} ответил ошибкой {e.code}{hint}")
