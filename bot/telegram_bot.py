"""Бот La Butaca (@labutaca_cine_bot): принимает предложения фильмов и отвечает.

Запускается GitHub Actions каждые ~5 минут. Секреты (Settings → Secrets → Actions):
TG_TOKEN — токен бота, TMDB_TOKEN — TMDB API Read Access Token, SB_URL и SB_KEY — Supabase.
Логика:
- /start, /help — подсказка;
- «/propongo Название — почему» — добавить без проверки;
- любое сообщение боту или комментарий в обсуждении канала: если первая строка похожа на название
  фильма/сериала на TMDB — добавить в Propuestas и ответить «✅ Принято»;
- не похоже: в личке — переспросить, в обсуждении канала — промолчать (чтобы не спамить на «круто!»).
"""
import difflib
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request

TG = os.environ.get("TG_TOKEN", "")
TMDB = os.environ.get("TMDB_TOKEN", "")
SB_URL = os.environ.get("SB_URL", "").rstrip("/")
SB_KEY = os.environ.get("SB_KEY", "")

HELP = ("🎬 Это бот La Butaca. Напишите название фильма или сериала — я добавлю его в «Propuestas» на сайте.\n"
        "Можно с пояснением на второй строке.\nЕсли не узнаю название: /propongo Название — почему")


def tg(method, **params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{TG}/{method}", data=data, timeout=30) as r:
        return json.load(r)


def norm(x):
    return re.sub(r"[^\w ]+", "", (x or "").lower()).strip()


def tmdb_match(text):
    q = re.sub(r"[«»\"'!?.,]+", " ", text).strip()
    if not q or len(q) > 80 or len(q.split()) > 8:
        return None
    req = urllib.request.Request("https://api.themoviedb.org/3/search/multi?language=ru-RU&query=" + urllib.parse.quote(q),
                                 headers={"Authorization": "Bearer " + TMDB})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=20)).get("results", [])
    except Exception:
        return None
    for r in res[:5]:
        if r.get("media_type") not in ("movie", "tv"):
            continue
        names = [n for n in (r.get("title"), r.get("name"), r.get("original_title"), r.get("original_name")) if n]
        best = max(difflib.SequenceMatcher(None, norm(q), norm(n)).ratio() for n in names)
        qs = {w[:5] for w in norm(q).split() if len(w) >= 4}
        ns = {w[:5] for n in names for w in norm(n).split() if len(w) >= 4}
        overlap = len(qs & ns) / len(qs) if qs else 0
        votes = r.get("vote_count") or 0
        if (best >= 0.8 and votes >= 5) or (len(qs) >= 2 and overlap >= 0.5 and votes >= 100):
            ru, orig = r.get("title") or r.get("name"), r.get("original_title") or r.get("original_name")
            return ru if ru == orig else f"{ru} ({orig})"
    return None


def add(title, author, note, uid):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"kind": "suggest", "key": "telegram-" + hashlib.sha1(uid.encode()).hexdigest()[:16], "updated_at": now,
           "data": {"title": title[:120], "author": author[:40], "note": note[:200], "source": "telegram", "at": now}}
    req = urllib.request.Request(SB_URL + "/rest/v1/marks", method="POST", data=json.dumps(row).encode(),
                                 headers={"apikey": SB_KEY, "Content-Type": "application/json",
                                          "Prefer": "resolution=ignore-duplicates"})
    urllib.request.urlopen(req, timeout=30)


def main():
    updates = tg("getUpdates", timeout=0, allowed_updates='["message"]').get("result", [])
    print(f"сообщений: {len(updates)}")
    last = None
    for u in updates:
        last = u["update_id"]
        m = u.get("message") or {}
        text = (m.get("text") or "").strip()
        chat = m.get("chat") or {}
        group = chat.get("type") in ("group", "supergroup")
        kind = chat.get("type")
        sender_chat = m.get("sender_chat") or {}
        # анонимный админ группы или пост от имени канала приходят от служебного бота — их не пропускаем
        real_bot = (m.get("from") or {}).get("is_bot") and not sender_chat
        if not text or real_bot or m.get("is_automatic_forward"):
            print(f"update {u['update_id']}: {kind}, пропуск (пусто/бот/пересланный пост канала)")
            continue
        reply = lambda t: tg("sendMessage", chat_id=chat["id"], text=t, reply_to_message_id=m["message_id"])
        cmd = text.split()[0].split("@")[0].lower()
        if cmd in ("/start", "/help"):
            if not group:
                reply(HELP)
            continue
        explicit = cmd == "/propongo"
        body = text.split(None, 1)[1] if explicit and len(text.split(None, 1)) > 1 else ("" if explicit else text)
        first, _, rest = body.partition("\n")
        title_part, _, why = first.partition("—")
        note = (why or rest).strip()
        title = title_part.strip() if explicit else tmdb_match(title_part.strip())
        if not title:
            print(f"update {u['update_id']}: {kind}, не похоже на название — {'переспрашиваю' if (not group or explicit) else 'молчу'}")
            if not group or explicit:
                reply("🤔 Не нашёл такой фильм или сериал. Напишите название точнее — или так:\n/propongo Название — почему")
            continue
        who = m.get("from") or {}
        if sender_chat:
            author = "La Butaca (админ)"
        else:
            author = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or who.get("username") or "кто-то"
        add(title, author, note, f"{chat.get('id')}:{m.get('message_id')}")
        print(f"update {u['update_id']}: {kind}, добавлено предложение")
        reply(f"✅ ¡Gracias! Принято: «{title}». Предложение уже на сайте La Butaca в разделе Propuestas.")
    if last is not None:
        tg("getUpdates", offset=last + 1, limit=1, timeout=0)  # подтверждаем обработанные сообщения


if __name__ == "__main__":
    if not all((TG, TMDB, SB_URL, SB_KEY)):
        print("Секреты ещё не добавлены (TG_TOKEN, TMDB_TOKEN, SB_URL, SB_KEY) — пропускаю")
    else:
        main()
