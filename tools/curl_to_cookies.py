#!/usr/bin/env python3
"""Превращает cookies, скопированные из DevTools, в cookies.txt формата Netscape.

Понимает три формы — что бы ни лежало в буфере обмена:

  1. «Copy as cURL» из вкладки Network        curl '...' -H 'cookie: a=1; b=2'
  2. значение заголовка Cookie                 a=1; b=2
     (в том числе с префиксом «cookie:»)
  3. строки таблицы Application → Cookies      name<TAB>value<TAB>domain<TAB>...

    pbpaste | python3 tools/curl_to_cookies.py > cookies.txt

Значения cookies скрипт не печатает — только имена, чтобы их можно было
показать кому-то без риска.
"""
import re
import sys
import time

# cookies без собственного срока: год вперёд достаточно, yt-dlp обновит сам
EXPIRY = int(time.time()) + 365 * 24 * 3600

# общие для сервисов Google живут на .google.com, остальные — на .youtube.com
GOOGLE_SCOPED = {"SID", "HSID", "SSID", "APISID", "SAPISID",
                 "__Secure-1PSID", "__Secure-3PSID",
                 "__Secure-1PAPISID", "__Secure-3PAPISID",
                 "__Secure-1PSIDTS", "__Secure-3PSIDTS",
                 "__Secure-1PSIDCC", "__Secure-3PSIDCC"}

# без этих YouTube считает сессию анонимной
AUTH_MARKERS = ("SID", "__Secure-3PSID", "LOGIN_INFO")


def from_curl(text: str) -> str | None:
    for pattern in (r"-H\s+'[Cc]ookie:\s*(.*?)'", r'-H\s+"[Cc]ookie:\s*(.*?)"',
                    r"-b\s+'(.*?)'", r'-b\s+"(.*?)"'):
        found = re.search(pattern, text, re.S)
        if found:
            return found.group(1)
    return None


def parse_header(header: str) -> list[tuple[str, str, str]]:
    header = re.sub(r"^\s*cookie:\s*", "", header.strip(), flags=re.I)
    header = header.replace("\\\n", " ").replace("\n", " ")

    cookies = []
    for pair in header.split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name:
            domain = ".google.com" if name in GOOGLE_SCOPED else ".youtube.com"
            cookies.append((domain, name.strip(), value.strip()))
    return cookies


def parse_table(text: str) -> list[tuple[str, str, str]]:
    cookies = []
    for line in text.splitlines():
        cols = line.split("\t")
        if len(cols) < 3 or cols[0] in ("Name", ""):
            continue
        name, value, domain = cols[0].strip(), cols[1].strip(), cols[2].strip()
        if not domain.startswith("."):
            domain = "." + domain.lstrip(".")
        cookies.append((domain, name, value))
    return cookies


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv
    text = open(args[0], encoding="utf-8").read() if args else sys.stdin.read()

    if not text.strip():
        sys.exit("Пусто. Точно ли cookies в буфере обмена?")

    if header := from_curl(text):
        cookies, source = parse_header(header), "curl"
    elif "\t" in text:
        cookies, source = parse_table(text), "таблица DevTools"
    else:
        cookies, source = parse_header(text), "заголовок Cookie"

    if not cookies:
        sys.exit(f"Не удалось разобрать как «{source}». Скопируй заново одним из трёх способов.")

    print("# Netscape HTTP Cookie File")
    for domain, name, value in cookies:
        # домен, поддомены, путь, только-https, срок, имя, значение
        print(f"{domain}\tTRUE\t/\tTRUE\t{EXPIRY}\t{name}\t{value}")

    names = [name for _, name, _ in cookies]
    print(f"Формат: {source}. Cookies: {len(names)}", file=sys.stderr)
    print(f"Имена: {', '.join(sorted(names))}", file=sys.stderr)

    missing = [m for m in AUTH_MARKERS if m not in names]
    if missing:
        print(f"ОШИБКА: нет {', '.join(missing)} — сессия не залогинена "
              "или в буфере не cookies (например, скопированная команда)", file=sys.stderr)
        # ненулевой код останавливает `&&`: мусор не уедет в S3
        if not force:
            sys.exit(1)
    else:
        print("Признаки входа в аккаунт на месте", file=sys.stderr)


if __name__ == "__main__":
    main()
