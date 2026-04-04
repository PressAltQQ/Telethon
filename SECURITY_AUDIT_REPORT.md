# Telethon Security Audit Report

**Дата:** 2026-04-03
**Версия:** Telethon (последний коммит из https://codeberg.org/Lonami/Telethon)
**Объём:** ~30,000 строк Python — полный аудит криптографии, сетевого протокола, сессий, валидации ввода и общей безопасности кода.

---

## Сводка

| Severity | Кол-во | Описание |
|----------|--------|----------|
| **CRITICAL** | 2 | Фундаментальные криптографические уязвимости в обмене ключами |
| **HIGH** | 9 | Timing-атаки, небезопасный TLS, хранение сессий, DoS через десериализацию |
| **MEDIUM** | 14 | Replay-атаки, HTML-инъекции, небезопасные зависимости, SSRF |
| **LOW** | 13 | Информационные утечки, второстепенные проблемы |
| **Итого** | **38** | |

---

## CRITICAL — Критические уязвимости

### C-1. Отсутствие валидации DH-простого числа при обмене ключами

- **Файл:** `telethon/network/authenticator.py:128-135`
- **Описание:** При Diffie-Hellman обмене ключами клиент получает `dh_prime` и `g` от сервера, но **никогда не проверяет**, что `dh_prime` — безопасное простое число, а `g` — допустимый генератор. Функция `check_prime_and_good()` уже существует в `password.py`, но не вызывается. Проверки диапазонов для `g_a`, `g_b` (строки 143-157) бессмысленны, если `dh_prime` контролируется атакующим.
- **Вектор атаки:** MITM-атакующий подменяет DH-параметры сервера слабым составным числом с известной структурой дискретного логарифма. Клиент вычисляет `g_b = pow(g, b, dh_prime)` с этим слабым числом, и атакующий восстанавливает общий секрет.
- **Исправление:**
```python
from telethon.password import check_prime_and_good
check_prime_and_good(server_dh_inner.dh_prime, server_dh_inner.g)
```

### C-2. SHA-1 хеш расшифрованных DH-данных читается, но не проверяется

- **Файл:** `telethon/network/authenticator.py:116-118`
- **Описание:** После расшифровки DH-параметров сервера код читает 20 байт (`reader.read(20) # hash sum`), но **никогда не сверяет** этот хеш с `sha1(server_dh_inner_data)`. AES-IGE не обеспечивает аутентифицированного шифрования — это malleable cipher. Без проверки хеша целостность DH-параметров не гарантирована.
- **Вектор атаки:** Атакующий перехватывает зашифрованный DH-ответ и выполняет целевые bit-flip модификации AES-IGE. Модифицированные значения `g_a` или `dh_prime` будут приняты без обнаружения.
- **Исправление:**
```python
dh_inner_bytes = bytes(server_dh_inner)
if hashlib.sha1(dh_inner_bytes).digest() != hash_bytes:
    raise SecurityError("DH inner data hash mismatch")
```

---

## HIGH — Высокая серьёзность

### H-1. Non-Constant-Time сравнение хешей (timing-атаки)

- **Файлы:**
  - `telethon/network/mtprotostate.py:172` — `msg_key != our_key.digest()[8:24]`
  - `telethon/crypto/cdndecrypter.py:104` — `sha256(data).digest() != cdn_hash.hash`
  - `telethon/crypto/authkey.py:63` — `other.key == self._key`
- **Описание:** Python `==` / `!=` для байтов short-circuit на первом отличающемся байте. Атакующий, способный измерять время ответа с достаточной точностью, может побайтово подбирать корректный `msg_key` или подменять CDN-контент.
- **Исправление:** Заменить на `hmac.compare_digest()` во всех трёх местах.

### H-2. SSL/TLS использует ADH-cipher без аутентификации сервера

- **Файл:** `telethon/network/connection/connection.py:61-65`
- **Описание:** Метод `_wrap_socket_ssl` использует устаревший `ssl.PROTOCOL_SSLv23` и cipher `ADH-AES256-SHA` (Anonymous Diffie-Hellman). ADH **не проверяет сертификат сервера** — нет `check_hostname`, нет `verify_mode=CERT_REQUIRED`. MITM-атака тривиальна.
- **Вектор атаки:** Любой сетевой атакующий перехватывает соединение, выполняет анонимный DH-обмен с обеими сторонами и ретранслирует/модифицирует весь трафик.
- **Исправление:** Использовать `ssl.create_default_context()` с проверкой сертификатов. Убрать ADH-cipher. Требовать TLS 1.2+.

### H-3. Проверки безопасности через `assert` (отключаются при `python -O`)

- **Файлы:**
  - `telethon/network/authenticator.py:32, 83-84, 101-102, 119-120, 180`
  - `telethon/network/mtprotoplainsender.py:42-52`
- **Описание:** Критические проверки типов серверных ответов и валидации протокола используют `assert`. При запуске с флагом оптимизации (`python -O`) все `assert` полностью удаляются.
- **Исправление:** Заменить `assert X` на `if not X: raise SecurityError(...)`.

### H-4. Сессионные файлы хранят auth_key без шифрования

- **Файл:** `telethon/sessions/sqlite.py:211-218`
- **Описание:** `SQLiteSession` сохраняет 256-байтный authorization key (полный доступ к аккаунту Telegram) как raw blob в SQLite без какого-либо шифрования. Таблица `entities` (строки 86-92) хранит номера телефонов и имена пользователей в открытом виде.
- **Вектор атаки:** Любой процесс на машине (малварь, скомпрометированная зависимость, другой пользователь) читает `.session` файл и получает полный доступ к аккаунту.
- **Исправление:** Шифровать auth_key через ключ, производный от пароля пользователя (PBKDF2/Argon2), или использовать OS-level secret storage (Keychain, DPAPI).

### H-5. Файлы сессий создаются без ограничения прав доступа

- **Файл:** `telethon/sessions/sqlite.py:256-260`
- **Описание:** При создании SQLite-файла сессии не устанавливаются ограничительные права доступа. Файл создаётся с umask по умолчанию (обычно 0644 — world-readable).
- **Исправление:** `os.chmod(self.filename, 0o600)` после создания файла.

### H-6. StringSession экспортирует auth_key в base64 без шифрования

- **Файл:** `telethon/sessions/string.py:52-63`
- **Описание:** `StringSession.save()` сериализует auth_key в base64 без шифрования и защиты паролем. Эта строка часто попадает в логи, `.env` файлы, CI/CD пайплайны, git-коммиты.
- **Исправление:** Поддержка опциональной симметричной шифровки с пользовательским паролем.

### H-7. GZip-бомба — неограниченная декомпрессия

- **Файл:** `telethon/tl/core/gzippacked.py:38, 42`
- **Описание:** `gzip.decompress()` вызывается без ограничения размера выходных данных. Малый gzip-payload (несколько КБ) может раскрыться в гигабайты, вызывая OOM.
- **Исправление:** Инкрементальная декомпрессия с лимитом (например, 16 МБ).

### H-8. Неограниченное чтение в TCP Intermediate — DoS через выделение памяти

- **Файл:** `telethon/network/connection/tcpintermediate.py:15-17`
- **Описание:** Длина пакета читается как signed 32-bit int, затем вызывается `reader.readexactly(length)` без проверки верхней границы. Злонамеренный сервер может отправить `length = 0x7FFFFFFF` (~2 ГБ).
- **Исправление:** Добавить `MAX_PACKET_SIZE` (например, 2 МБ) и проверять до вызова `readexactly`.

### H-9. Пример quart_login с hardcoded secret и открытым паролем

- **Файл:** `telethon_examples/quart_login.py:42-44, 60`
- **Описание:** Пароль 2FA отображается как `type='text'`. Ключ сессии Flask/Quart захардкожен: `'CHANGE THIS TO SOMETHING SECRET'`.
- **Исправление:** `type='password'`, секрет из `os.urandom(32)`.

### Примечание к H-4 и H-6

H-4 (шифрование auth_key в SQLite) и H-6 (шифрование StringSession) являются **известными ограничениями**, требующими изменения API для полноценного исправления (добавление парольного шифрования сессий). Текущие меры смягчения:
- H-5 исправлено: файлы сессий создаются с правами `0600`
- MCP-сервер (`02_mcp_server.py`) использует StringSession через переменные окружения, а не файлы на диске
- Рекомендация: хранить StringSession в менеджере секретов (Keychain, Vault, AWS Secrets Manager)

---

## MEDIUM — Средняя серьёзность

### M-1. Соль и sequence number не проверяются при расшифровке сообщений

- **Файл:** `telethon/network/mtprotostate.py:176-192`
- **Описание:** `decrypt_message_data` имеет `TODO: Check salt, session_id and sequence_number`. Session_id проверяется (строка 178), но `salt` читается и игнорируется, `sequence_number` не валидируется.
- **Вектор атаки:** Replay-атака с устаревшими солями.

### M-2. Обход защиты от replay-атак

- **Файл:** `telethon/network/mtprotostate.py:186-189`
- **Описание:** Deque `_recent_remote_ids` имеет `maxlen=500`. ID старше 500 сообщений выпадают. Для `remote_msg_id > _highest_remote_id` проверка дубликатов пропускается.
- **Исправление:** Всегда проверять `_recent_remote_ids`; использовать set вместо deque.

### M-3. Баг MTProxy: пустой срез `random[4:4]`

- **Файл:** `telethon/network/connection/tcpmtproxy.py:52`
- **Описание:** `random[4:4]` — всегда `b''`, условие всегда True. Должно быть `random[4:8]` (как в `tcpobfuscated.py:28`).
- **Исправление:** Изменить `random[4:4]` на `random[4:8]`.

### M-4. Неограниченный счётчик MessageContainer

- **Файл:** `telethon/tl/core/messagecontainer.py:39`
- **Описание:** `MAXIMUM_LENGTH = 100` определён, но не проверяется при парсинге входящих контейнеров.
- **Исправление:** `if count > cls.MAXIMUM_LENGTH: raise SecurityError(...)`.

### M-5. Неограниченная десериализация векторов

- **Файл:** `telethon/extensions/binaryreader.py:147, 165`
- **Описание:** Счётчик элементов вектора читается из потока без верхней границы.
- **Исправление:** Добавить разумный максимум (например, 1,000,000).

### M-6. Отсутствие ограничения глубины рекурсии при десериализации

- **Файл:** `telethon/extensions/binaryreader.py:158`
- **Описание:** GzipPacked → decompress → BinaryReader → tgread_object → GzipPacked... — бесконечная рекурсия возможна.
- **Исправление:** Thread-local счётчик глубины с лимитом ~16.

### M-7. HTML-инъекция в `unparse()` — MessageEntityUrl/Email без escape

- **Файл:** `telethon/extensions/html.py:158-159`
- **Описание:** `MessageEntityUrl` и `MessageEntityEmail` вставляют текст в `href` **без экранирования**, в отличие от `MessageEntityTextUrl` (строка 160), который правильно использует `escape(e.url)`.
- **Вектор атаки:** Сообщение с URL `http://a" onmouseover="alert(1)` → XSS при рендеринге HTML.
- **Исправление:** `escape(t)` в обоих лямбдах.

### M-8. HTML-инъекция через атрибут language в `<pre>` тегах

- **Файл:** `telethon/extensions/html.py:151-156`
- **Описание:** `e.language` вставляется в `class='language-{}'` без экранирования.
- **Исправление:** `escape(e.language)`.

### M-9. `webbrowser.open()` с непроверенным URL

- **Файл:** `telethon/tl/custom/messagebutton.py:122`
- **Описание:** При `open_url=True` клик по кнопке вызывает `webbrowser.open(self.button.url)` с URL, полностью контролируемым отправителем.
- **Исправление:** Проверять, что схема URL — `http` или `https`.

### M-10. SSRF через `send_file` с URL

- **Файл:** `telethon/client/uploads.py:817-821`
- **Описание:** URL, переданный в `send_file`, передаётся серверу Telegram как `InputMediaPhotoExternal`. Telegram-сервер выполнит HTTP-запрос на этот URL.
- **Исправление:** Документировать риск; добавить валидацию/allowlist при использовании с пользовательским вводом.

### M-11. Незакреплённые версии зависимостей

- **Файл:** `requirements.txt:1-2`, `optional-requirements.txt`
- **Описание:** `pyaes` и `rsa` указаны без версий. Supply chain атака может подставить малициозную версию.
- **Исправление:** Закрепить версии: `rsa==4.9`, `pyaes==1.6.1`.

### M-12. SQLite `check_same_thread=False` без блокировок

- **Файл:** `telethon/sessions/sqlite.py:260`
- **Описание:** Отключена проверка потоков SQLite, но нет `threading.Lock` для защиты операций записи.
- **Исправление:** Добавить `Lock` или включить WAL-режим.

### M-13. libssl: нет проверки возвращаемых значений и argtypes

- **Файл:** `telethon/crypto/libssl.py:108, 130`
- **Описание:** `AES_set_*_key` возвращает 0 при успехе и отрицательное значение при ошибке — код не проверяет. Также не определены `.argtypes`/`.restype` для ctypes-вызовов, что может привести к memory corruption на 64-bit системах.

### M-14. Hardcoded `retry_id=0` при DH-обмене

- **Файл:** `telethon/network/authenticator.py:163`
- **Описание:** Всегда отправляется `retry_id=0` вместо `auth_key_aux_hash` от предыдущей неудачной попытки. Retry полностью сломан — при `DhGenRetry` выбрасывается `AssertionError`.

---

## LOW — Низкая серьёзность

### L-1. `random.randint` вместо CSPRNG в факторизации
- **Файл:** `telethon/crypto/factorization.py:1, 24`
- Mersenne Twister предсказуем. Использовать `secrets.randbelow()`.

### L-2. AESModeCTR обращается к приватному состоянию pyaes
- **Файл:** `telethon/crypto/aesctr.py:24`
- `self._aes._counter._counter = list(iv)` — обновление pyaes сломает IV-инициализацию.

### L-3. SHA-1 повсеместно в auth key exchange
- **Файлы:** `rsa.py:47`, `authkey.py:39,57`, `authenticator.py:95,167`
- Ограничение протокола MTProto — нельзя исправить на уровне клиента.

### L-4. Неограниченное чтение в TCP Abridged/Full/HTTP
- **Файлы:** `tcpabridged.py:19-24` (до ~64 МБ), `tcpfull.py:43`, `http.py:31`

### L-5. HTTP header injection через IP/port
- **Файл:** `telethon/network/connection/http.py:14-21`

### L-6. Race condition в reconnection-логике
- **Файл:** `telethon/network/mtprotosender.py:423-436`
- `TODO` в коде признаёт проблему.

### L-7. Неограниченная манипуляция sequence number
- **Файл:** `telethon/network/mtprotosender.py:782-787`
- Сервер может двигать sequence counter через BadMsgNotification.

### L-8. vCard-инъекция через newline в контактах
- **Файл:** `telethon/client/downloads.py:958-967`
- `;` удаляется, но `\n`, `\r` — нет.

### L-9. ReDoS в markdown regex
- **Файл:** `telethon/extensions/markdown.py:25`
- Ограничено длиной сообщений Telegram (~4096 символов).

### L-10. `shell=True` в setup.py
- **Файл:** `setup.py:196-198`

### L-11. Серверные fingerprints в сообщениях об ошибках
- **Файл:** `telethon/network/authenticator.py:68-73`

### L-12. Файловые пути в логах при ошибках hachoir
- **Файл:** `telethon/utils.py:670`

### L-13. Event handler regex из пользовательского ввода → ReDoS
- **Файлы:** `events/newmessage.py:78`, `events/callbackquery.py:73`, `events/inlinequery.py:55`

---

## Позитивные находки

| Область | Статус |
|---------|--------|
| **Pickle** | Не используется в production-коде (только в тестах) — **нет RCE** |
| **eval/exec/os.system** | Не найдены нигде в кодовой базе |
| **SRP (2FA)** | Корректная реализация с PBKDF2-HMAC-SHA512, 100K итераций |
| **Path traversal** | Защита через `os.path.basename()` (ref: #4713) |
| **API credentials** | Все примеры используют `os.environ.get()` или `input()` |
| **Криптографический RNG** | `os.urandom()` везде, кроме факторизации |
| **MTProto 2.0 padding** | Корректная реализация (12-1024 байт рандомного padding) |
| **SQL-запросы** | Параметризованные запросы для всех пользовательских данных |
| **Temp-файлы** | Не используются — нет insecure temp file issues |

---

## Приоритетный план исправлений

### Немедленно (CRITICAL + HIGH с простыми фиксами)

1. **C-2** — Добавить проверку SHA-1 хеша DH inner data (~5 строк)
2. **C-1** — Вызвать `check_prime_and_good()` для DH prime (функция уже есть)
3. **H-3** — Заменить `assert` на `if/raise SecurityError` в authenticator и plain sender
4. **H-1** — Заменить `==`/`!=` на `hmac.compare_digest()` в 3 местах
5. **M-3** — Исправить `random[4:4]` → `random[4:8]` (1 символ)

### Краткосрочно (HIGH — защита данных)

6. **H-5** — Добавить `os.chmod(filename, 0o600)` для session-файлов
7. **H-4/H-6** — Документировать риски; добавить опциональное шифрование сессий
8. **H-2** — Заменить `ssl.PROTOCOL_SSLv23` + `ADH` на `ssl.create_default_context()`

### Среднесрочно (HIGH + MEDIUM — DoS-защита)

9. **H-7** — Ограничить размер gzip-декомпрессии (16 МБ)
10. **H-8** — Добавить `MAX_PACKET_SIZE` во все connection codecs
11. **M-4/M-5** — Ограничить MessageContainer count и vector size
12. **M-6** — Добавить recursion depth guard

### Среднесрочно (MEDIUM — инъекции и прочее)

13. **M-7/M-8** — Добавить `escape()` в HTML unparse
14. **M-1/M-2** — Проверять salt и улучшить replay-защиту
15. **M-11** — Закрепить версии зависимостей
16. **M-13** — Добавить argtypes/restype и проверки возвращаемых значений в libssl
