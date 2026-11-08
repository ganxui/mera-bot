"""Per-user MAX demos with their own temporary database and virtual accounts."""
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field

from mera import Store, button, reply


@dataclass
class DemoSession:
    engine: object
    actor: int = 1
    active: bool = True
    generation: str = field(default_factory=lambda: secrets.token_hex(5))
    reset_token: str = ''


class DemoMixin:
    def __init__(self, *args, max_demo=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_demo = max_demo
        self.demos = OrderedDict()

    def menu(self, uid, prefix=''):
        return self.demo_link(super().menu(uid, prefix))

    def plan(self, uid, offset=0, page=0):
        return self.demo_link(super().plan(uid, offset, page))

    def profile(self, uid, prefix=''):
        return self.demo_link(super().profile(uid, prefix))

    def demo_link(self, messages):
        if self.max_demo:
            rows = messages[0]['attachments'][0]['payload']['buttons']
            if not any(item.get('payload') == 'demo:enter' for row in rows for item in row):
                rows.append([button('Демо-режим', 'demo:enter')])
        return messages

    def route_input(self, uid, text, is_callback):
        if not self.max_demo:
            return super().route_input(uid, text, is_callback)
        if text == '/demo' or text == 'demo:enter' and is_callback is True:
            session = self.demos.get(uid) or self.new_demo(uid)
            session.active = True
            session.reset_token = ''
            self.demos.move_to_end(uid)
            return self.demo_screen(session, '/menu', False)
        session = self.demos.get(uid)
        if not session or not session.active:
            if text.startswith('demo:') or text in ('/demo_exit', '/demo_reset', '/reset_demo'):
                return self.demo_link(self.menu(uid, 'Откройте демо через /demo.\n\n'))
            return super().route_input(uid, text, is_callback)
        self.demos.move_to_end(uid)
        if text == '/demo_exit' or text == 'demo:exit' and is_callback is True:
            session.active = False
            session.reset_token = ''
            messages = super().route_input(uid, '/menu', False)
            messages[0]['text'] = 'Демо закрыто.\n\n' + messages[0]['text']
            return messages
        if text in ('/demo_reset', '/reset_demo') or text == 'demo:reset' and is_callback is True:
            session.reset_token = secrets.token_hex(5)
            return self.demo_card(session, 'Удалить тестовые классы, задания и прогресс?',
                                  [[button('Сбросить демо', f'demo:reset_ok:{session.reset_token}')],
                                   [button('Отмена', 'demo:enter')]], 'СБРОСИТЬ\nДЕМО?')
        if is_callback is True and text.startswith('demo:reset_ok:'):
            if not session.reset_token or text != f'demo:reset_ok:{session.reset_token}':
                return self.demo_screen(session, '/menu', False, 'Подтверждение устарело.\n\n')
            session = self.new_demo(uid)
            return self.demo_screen(session, '/start', False, 'Демо сброшено.\n\n')
        if text == 'demo:accounts' and is_callback is True:
            session.reset_token = ''
            rows = [[button(f'Аккаунт {actor}', f'demo:actor:{actor}')] for actor in (1, 2, 3)]
            rows.append([button('Назад', 'demo:enter')])
            return self.demo_card(session, 'Выберите тестовый аккаунт. Код класса можно перенести между аккаунтами.',
                                  rows, 'АККАУНТЫ\nДЕМО')
        if is_callback is True and text in ('demo:actor:1', 'demo:actor:2', 'demo:actor:3'):
            session.reset_token = ''
            session.actor = int(text[-1])
            return self.demo_screen(session, '/menu', False)
        session.reset_token = ''
        if is_callback is True:
            prefix = f'demo:do:{session.generation}:{session.actor}:'
            if not text.startswith(prefix):
                return self.demo_screen(session, '/menu', False, 'Откройте актуальную карточку.\n\n')
            text = text[len(prefix):]
        return self.demo_screen(session, text, is_callback)

    def new_demo(self, uid):
        previous = self.demos.pop(uid, None)
        if previous:
            previous.engine.store.close()
        if len(self.demos) >= 32:
            _, oldest = self.demos.popitem(last=False)
            oldest.engine.store.close()
        session = DemoSession(type(self)(Store(':memory:'), today=self.today, estimator=self.estimator,
                                        command_parser=self.command_parser))
        self.demos[uid] = session
        return session

    def demo_screen(self, session, text, is_callback, prefix=''):
        messages = session.engine.handle(session.actor, text, is_callback=is_callback)
        for message in messages:
            message['text'] = f'Демо · аккаунт {session.actor}\n\n' + prefix + message['text']
            attachments = message.setdefault('attachments', [])
            keyboard = next((item for item in attachments if item['type'] == 'inline_keyboard'), None)
            if keyboard is None:
                keyboard = {'type': 'inline_keyboard', 'payload': {'buttons': []}}
                attachments.append(keyboard)
            rows = keyboard['payload']['buttons']
            for row in rows:
                for item in row:
                    if item['type'] == 'callback':
                        item['payload'] = f'demo:do:{session.generation}:{session.actor}:' + item['payload']
            rows.extend([[button('Аккаунты демо', 'demo:accounts')], [button('Сбросить демо', 'demo:reset')],
                         [button('Выйти из демо', 'demo:exit')]])
        return messages

    def demo_card(self, session, text, rows, title):
        message = reply(f'Демо · аккаунт {session.actor}\n\n{text}', rows)
        message['_visual'] = {'kind': 'menu', 'title': title, 'subtitle': 'Тестовые данные'}
        return [message]
