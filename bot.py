"""MAX transport. Run `python bot.py --demo` without credentials."""
import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mera import Engine, Store
from cards import model_hash, render_card


ROOT = Path(__file__).resolve().parent


class ProcessLock:
    """Один Polling-процесс на базу; ОС освобождает блокировку даже при аварии."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise ValueError('Бот с этой базой уже запущен. Оставьте только один процесс MAX.') from None
        return self

    def __exit__(self, *_):
        if self.file:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            self.file.close()
            self.file = None


def load_env(path=ROOT / '.env'):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


class ApiError(Exception):
    def __init__(self, status, code=''):
        self.status, self.code = status, code
        super().__init__(f'MAX API: HTTP {status}. Содержимое ответа скрыто.')


class MaxAPI:
    def __init__(self, token, base='https://platform-api2.max.ru'):
        if not base.startswith('https://'):
            raise ValueError('MAX_API_BASE должен использовать HTTPS.')
        self.token, self.base = token, base.rstrip('/')
        self.media_context = ssl.create_default_context()
        self.context = self.media_context
        if urllib.parse.urlsplit(self.base).hostname == 'platform-api2.max.ru':
            self.context = ssl.create_default_context()
            self.context.load_verify_locations(cafile=str(ROOT / 'assets/certs/max-root.crt'))
        self.last_request = 0.0

    def pace(self):
        # MAX: максимум 2 сообщения/с в один диалог. Общая очередь консервативнее.
        pause = 0.55 - (time.monotonic() - self.last_request)
        if pause > 0:
            time.sleep(pause)
        self.last_request = time.monotonic()

    def call(self, method, path, params=None, body=None):
        self.pace()
        url = self.base + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': self.token, 'Content-Type': 'application/json', 'User-Agent': 'MeraBot/0.1'})
        try:
            with urllib.request.urlopen(request, timeout=40, context=self.context) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                error_code = str(json.loads(exc.read()).get('code', ''))
            except (ValueError, AttributeError):
                error_code = ''
            raise ApiError(exc.code, error_code) from None

    def send(self, uid, message):
        for attempt in range(4):
            try:
                return self.call('POST', '/messages', {'user_id': uid}, message)
            except ApiError as exc:
                if exc.code != 'attachment.not.ready' or attempt == 3:
                    raise
                time.sleep(0.7 * (attempt + 1))

    def upload(self, path):
        result = self.call('POST', '/uploads', {'type': 'image'})
        upload_url = result['url']
        if not upload_url.startswith('https://'):
            raise ValueError('MAX вернул небезопасный адрес загрузки изображения.')
        boundary = 'MeraImage' + os.urandom(12).hex()
        content = Path(path).read_bytes()
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="mera.png"\r\n'
                'Content-Type: image/png\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        self.pace()
        # Upload URL подписан платформой. Токен бота на медиа-хост не передаём.
        request = urllib.request.Request(upload_url, data=data, method='POST',
            headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        try:
            with urllib.request.urlopen(request, timeout=40, context=self.media_context) as response:
                uploaded = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code) from None
        if uploaded.get('photos'):
            return {'photos': uploaded['photos']}
        if uploaded.get('token'):
            return {'token': uploaded['token']}
        if result.get('token'):
            return {'token': result['token']}
        raise ValueError('MAX не вернул дескриптор загруженной карточки.')

    def delete(self, mid):
        return self.call('DELETE', '/messages', {'message_id': mid})


def decode_update(update):
    kind = update.get('update_type')
    if kind == 'bot_started':
        user = update.get('user') or {}
        uid = user.get('user_id')
        # Ответ всегда в личный диалог пользователя, а не в общий чат.
        return (uid, '/start') if uid is not None and not user.get('is_bot') else None
    message = update.get('message') or {}
    if message.get('recipient', {}).get('chat_type') != 'dialog':
        return None
    if kind == 'message_created':
        sender = message.get('sender') or {}
        text = (message.get('body') or {}).get('text')
        if sender.get('user_id') is not None and not sender.get('is_bot'):
            return sender['user_id'], text or '/help'
    if kind == 'message_callback':
        callback = update.get('callback') or {}
        user = callback.get('user') or {}
        if user.get('user_id') is not None and not user.get('is_bot'):
            return user['user_id'], callback.get('payload') or '/menu'
    return None


def event_key(update):
    kind = update.get('update_type')
    if kind == 'message_callback':
        callback_id = (update.get('callback') or {}).get('callback_id')
        if callback_id:
            return f'callback:{callback_id}'
    if kind == 'message_created':
        mid = ((update.get('message') or {}).get('body') or {}).get('mid')
        if mid:
            return f'message:{mid}'
    return hashlib.sha256(json.dumps(update, sort_keys=True).encode()).hexdigest()


def prepare_message(store, api, message):
    body = {key: value for key, value in message.items() if not key.startswith('_')}
    model = message.get('_visual') or {'kind':'menu','title':'ВСЁ\nПО ПЛАНУ','subtitle':'Учёба в своём темпе'}
    if model:
        key = model_hash(model)
        cached = store.db.execute('SELECT payload FROM media_cache WHERE hash=?', (key,)).fetchone()
        if cached:
            payload = json.loads(cached['payload'])
        else:
            image = render_card(model, ROOT / 'data/cards')
            payload = api.upload(image)
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO media_cache VALUES (?,?)', (key, json.dumps(payload)))
        body['attachments'] = [{'type': 'image', 'payload': payload}] + body.get('attachments', [])
    return body


def flush_outbox(store, api):
    for row in store.db.execute('SELECT * FROM outbox WHERE sent=0 ORDER BY id').fetchall():
        message = json.loads(row['value'])
        body = prepare_message(store, api, message)
        response = api.send(row['user_id'], body)
        mid = ((response.get('message') or response).get('body') or {}).get('mid')
        if not mid:
            raise ValueError('MAX не вернул ID доставленной карточки. Очередь сохранена.')
        with store.db:
            previous = store.db.execute('SELECT mid FROM screens WHERE user_id=?', (row['user_id'],)).fetchone()
            for old in (previous['mid'] if previous else None, message.get('_source_mid')):
                if old and old != mid:
                    store.db.execute('INSERT OR IGNORE INTO cleanup VALUES (?)', (old,))
            store.db.execute('INSERT OR REPLACE INTO screens VALUES (?,?)', (row['user_id'], mid))
            store.db.execute('UPDATE outbox SET sent=1 WHERE id=?', (row['id'],))
    # Старую карточку удаляем только после успешной доставки новой.
    for row in store.db.execute('SELECT mid FROM cleanup').fetchall():
        try:
            result = api.delete(row['mid'])
            success = result.get('success', True)
        except ApiError as exc:
            if exc.status in (400, 404):
                success = True  # Сообщение уже удалено / стало недоступно.
            else:
                raise
        if success:
            with store.db:
                store.db.execute('DELETE FROM cleanup WHERE mid=?', (row['mid'],))


def run_polling(store, api):
    api.call('GET', '/me')
    subscriptions = api.call('GET', '/subscriptions').get('subscriptions', [])
    if subscriptions:
        raise ValueError('У бота есть Webhook-подписка. Polling одновременно с Webhook не работает. '
                         'Не удаляйте подписку, пока не уточните, кто её использует.')
    from estimation import DeepSeekCommandParser, DeepSeekEstimator

    estimate_key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    estimator = DeepSeekEstimator(estimate_key) if estimate_key else None
    command_parser = DeepSeekCommandParser(estimate_key) if estimate_key else None
    engine = Engine(store, estimator=estimator, command_parser=command_parser)
    print(f'Автоматическая оценка времени: {"включена" if estimator else "выключена"}.', flush=True)
    print(f'Mera Agent: {"расширенный" if command_parser else "базовый"} режим.', flush=True)
    print('МЕРА запущена в MAX (демонстрационный Long Polling). Ctrl+C — остановить.', flush=True)
    delay = 2
    while True:
        try:
            flush_outbox(store, api)
            row = store.db.execute("SELECT value FROM metadata WHERE key='marker'").fetchone()
            params = {'timeout': 25, 'limit': 100, 'types': 'bot_started,message_created,message_callback'}
            if row:
                params['marker'] = row['value']
            result = api.call('GET', '/updates', params)
            for update in result.get('updates', []):
                decoded = decode_update(update)
                if not decoded:
                    continue
                source_mid = ((update.get('message') or {}).get('body') or {}).get('mid') if update.get('update_type') == 'message_callback' else None
                engine.handle(*decoded, event_id=event_key(update), source_mid=source_mid,
                              is_callback=update.get('update_type') == 'message_callback')
                callback_id = (update.get('callback') or {}).get('callback_id')
                if callback_id:
                    try:
                        api.call('POST', '/answers', {'callback_id': callback_id}, {})
                    except (ApiError, urllib.error.URLError, TimeoutError):
                        pass  # Ответ уже сохранён в outbox; подтверждение кнопки необязательно.
            if result.get('marker') is not None:
                with store.db:
                    store.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('marker', str(result['marker'])))
            flush_outbox(store, api)
            delay = 2
        except ApiError as exc:
            if exc.status in (401, 403):
                raise
            if exc.status in (400, 404):
                raise ValueError(f'MAX отклонил запрос (HTTP {exc.status}). Очередь сохранена; '
                                 'проверьте MAX_API_BASE и совместимость API.') from None
            print(f'Временная ошибка MAX HTTP {exc.status}; повтор через {delay} с.', flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            print(f'Ошибка сети или TLS. Очередь сохранена; повтор через {delay} с.', flush=True)
            time.sleep(delay)
            delay = min(delay * 2, 30)


def run_demo():
    store = Store(':memory:')
    engine = Engine(store)
    uid = 1
    buttons = []
    print('МЕРА · локальное демо без MAX и токена\n'
          '/user 1 — администратор, /user 2 — учитель, /user 3 — ученик.\n'
          'Введите /номер кнопки или текст. /quit — выход. Данные этого демо временные.\n')

    def show(messages):
        nonlocal buttons
        buttons = []
        for message in messages:
            print('\n' + message['text'])
            for attachment in message.get('attachments', []):
                for row in attachment['payload']['buttons']:
                    for b in row:
                        buttons.append(b)
                        print(f'  [/{len(buttons)}] {b["text"]}')

    show(engine.handle(uid, '/start'))
    try:
        while True:
            text = input(f'\nuser {uid}> ').strip()
            if text == '/quit':
                break
            if text.startswith('/user '):
                try:
                    uid = int(text.split()[1])
                except (ValueError, IndexError):
                    print('Пример: /user 2')
                    continue
                show(engine.handle(uid, '/menu'))
                continue
            is_callback = text.startswith('/') and text[1:].isdigit() and 1 <= int(text[1:]) <= len(buttons)
            if is_callback:
                text = buttons[int(text[1:]) - 1]['payload']
            show(engine.handle(uid, text, is_callback=is_callback))
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        store.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='МЕРА · учебная нагрузка в MAX')
    parser.add_argument('--demo', action='store_true', help='Локальный диалог без токена; данные временные')
    parser.add_argument('--check', action='store_true', help='Проверить токен через GET /me без запуска')
    args = parser.parse_args()
    if args.demo:
        run_demo()
        return
    load_env()
    if not args.check and os.environ.get('MERA_PAUSED') == '1':
        print('МЕРА ждёт переноса базы. Для запуска отключите MERA_PAUSED и перезапустите процесс.', flush=True)
        while True:
            time.sleep(30)
    token = os.environ.get('MAX_BOT_TOKEN', '')
    if not token:
        print('Токен пока не настроен. Заполните MAX_BOT_TOKEN в локальном .env.\n'
              'Без токена можно запустить: python bot.py --demo')
        raise SystemExit(2)
    api = MaxAPI(token, os.environ.get('MAX_API_BASE', 'https://platform-api2.max.ru'))
    store = None
    try:
        if args.check:
            api.call('GET', '/me')
            print('Токен принят MAX. Секрет и данные ответа не выводятся.')
            return
        db_path = Path(os.environ.get('MERA_DB', 'data/mera.sqlite3'))
        if not db_path.is_absolute():
            db_path = ROOT / db_path
        with ProcessLock(str(db_path) + '.lock'):
            store = Store(db_path)
            run_polling(store, api)
    except KeyboardInterrupt:
        print('\nМЕРА остановлена. Данные сохранены.')
    except (ApiError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        print('Не удалось подключиться к MAX. Проверьте сеть, MAX_API_BASE и доверенные TLS-сертификаты.', file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if store:
            store.close()


if __name__ == '__main__':
    main()
