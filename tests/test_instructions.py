import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mera import Engine, Store


def controls(messages):
    return {item['payload'] for message in messages for attachment in message.get('attachments', [])
            for row in attachment['payload']['buttons'] for item in row}


class InstructionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(self.store)
        self.engine.handle(1, 'create')
        self.engine.handle(1, '9А')
        head = self.store.member(1)
        for uid, code in ((2, head['teacher_code']), (3, head['student_code'])):
            self.engine.handle(uid, 'join')
            self.engine.handle(uid, code)

    def test_help_and_menu_use_the_current_role(self):
        roles = ((1, 'admin', 'Классный руководитель', 'Ученики и отчёты'),
                 (2, 'teacher', 'Учитель', 'Публикация задания'),
                 (3, 'student', 'Ученик', 'Настройка времени'))
        for uid, role, label, topic in roles:
            with self.subTest(role=role):
                self.assertIn('guide', controls(self.engine.handle(uid, '/menu')))
                self.assertIn('guide', controls(self.engine.handle(uid, '/profile')))
                menu = self.engine.handle(uid, '/help')
                self.assertIn(label, menu[0]['text'])
                self.assertIn('Инструкции по использованию бота', menu[0]['text'])
                self.assertIn(topic, menu[0]['text'])
                self.assertEqual(menu[0]['format'], 'html')
                self.assertEqual(controls(menu), {'menu'})
                self.assertNotIn('—', menu[0]['text'])

    def test_guest_guides_offer_role_selection_and_exclude_working_shortcuts(self):
        picker = self.engine.handle(4, '/help')
        self.assertIn('guide:admin', controls(picker))
        detail = self.engine.handle(4, 'guide:admin:reports', is_callback=True)
        self.assertIn('Ученики и отчёты', detail[0]['text'])
        self.assertNotIn('students:0', controls(detail))
        self.assertIn('menu', controls(detail))

    def test_agent_topic_is_available_for_each_role(self):
        for uid, role in ((1, 'admin'), (2, 'teacher'), (3, 'student')):
            with self.subTest(role=role):
                topic = self.engine.handle(uid, f'guide:{role}:agent', is_callback=True)
                self.assertIn('<b>Примеры команд</b>', topic[0]['text'])
                self.assertIn('Mera Agent', topic[0]['text'])
                self.assertNotIn('—', topic[0]['text'])
