import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mera import Engine, Store


def callbacks(messages):
    return [button['payload'] for message in messages for attachment in message.get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row]


class MultiClassTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.parser = Mock(return_value=None)
        self.engine = Engine(self.store, today=lambda: date(2026, 11, 8), command_parser=self.parser, max_demo=True)

    def send(self, uid, text, callback=False):
        return self.engine.handle(uid, text, is_callback=callback)

    def create(self, uid, name):
        self.send(uid, 'enroll:head', True)
        self.send(uid, name)
        return self.store.member(uid)

    def test_reported_subject_command_works_without_provider(self):
        head = self.create(1, '9А')
        self.send(1, '/agent')
        result = self.send(1, 'Добавь классу новый предмет "Русский язык как иностранный"')
        self.assertIn('Русский язык как иностранный', result[0]['text'])
        self.assertEqual(self.store.class_subjects(head['class_id'])[0]['name'], 'Русский язык как иностранный')
        self.parser.assert_not_called()

    def test_agent_selects_only_joined_classes_and_keeps_subject_per_class(self):
        a = self.create(1, '9А')
        b = self.create(2, '8Б')
        self.send(1, 'enroll:teacher', True)
        self.send(1, 'Английский язык')
        self.send(1, b['teacher_code'])
        self.assertEqual(self.store.member(1)['role'], 'teacher')
        self.assertIn('Английский язык', self.send(1, '/profile')[0]['text'])
        classes = self.send(1, '/agent Покажи мои классы')[0]['text']
        self.assertIn('9А · классный руководитель', classes)
        self.assertIn('8Б · учитель', classes)
        self.send(1, '/agent Добавь классу 9А новый предмет «Робототехника»')
        self.assertEqual(self.store.member(1)['class_id'], a['class_id'])
        self.assertIn('Робототехника', self.send(1, '/agent Покажи предметы класса 9А')[0]['text'])
        self.assertIn('Английский язык', self.send(1, '/agent Покажи кабинет 8Б')[0]['text'])
        self.send(1, '/agent Добавь предмет Астрономия в 7В')
        self.assertEqual(self.store.member(1)['class_id'], b['class_id'])
        self.assertNotIn('Астрономия', self.send(1, '/agent Покажи предметы класса 8Б')[0]['text'])
        self.assertIn('9А', self.send(1, '/agent Покажи нагрузку всех классов')[0]['text'])

    def test_class_switch_invalidates_draft_and_prevents_stale_class_settings(self):
        a = self.create(1, '9А')
        b = self.create(1, '8Б')
        draft = self.send(1, '/agent Добавь 9А задание по алгебре к завтра на 30 минут: № 12')
        confirm = next(action for action in callbacks(draft) if action.startswith('confirm:'))
        self.send(1, f'classes:switch:{b["class_id"]}', True)
        self.send(1, confirm, True)
        self.assertFalse(self.store.tasks(a['class_id']))
        self.assertFalse(self.store.tasks(b['class_id']))
        prompt = self.send(1, '/agent Установи порог класса 9А на 120 минут')
        save = next(action for action in callbacks(prompt) if action.startswith('limit_class:'))
        self.send(1, f'classes:switch:{b["class_id"]}', True)
        self.send(1, save, True)
        self.assertEqual(self.store.member(1)['daily_limit'], 180)

    def test_max_demo_reset_is_isolated_and_old_buttons_expire(self):
        real = self.create(1, '9А')
        demo = self.send(1, '/demo')
        self.assertIn('выберите свою роль', demo[0]['text'])
        enroll = next(action for action in callbacks(demo) if action.endswith('enroll:head'))
        self.send(1, enroll, True)
        self.send(1, '7Б')
        first = self.engine.demos[1]
        self.addCleanup(lambda: self.engine.demos[1].engine.store.close())
        self.assertEqual(first.engine.store.member(1)['name'], '7Б')
        self.send(1, 'demo:accounts', True)
        self.send(1, 'demo:actor:2', True)
        self.assertIsNone(first.engine.store.member(2))
        reset = self.send(1, 'demo:reset', True)
        confirm = next(action for action in callbacks(reset) if action.startswith('demo:reset_ok:'))
        self.send(1, 'demo:enter', True)
        self.send(1, confirm, True)
        self.assertIs(self.engine.demos[1], first)
        reset = self.send(1, '/demo_reset')
        confirm = next(action for action in callbacks(reset) if action.startswith('demo:reset_ok:'))
        result = self.send(1, confirm, True)
        self.assertIn('Демо сброшено', result[0]['text'])
        self.assertIsNone(self.engine.demos[1].engine.store.member(1))
        self.send(1, enroll, True)
        self.assertFalse(self.engine.demos[1].engine.store.state(1))
        self.send(1, '/demo_exit')
        self.assertEqual(self.store.member(1)['class_id'], real['class_id'])
        self.assertIn('demo:enter', callbacks(self.send(1, '/menu')))
