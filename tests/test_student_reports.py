import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mera import Engine, Store


def payloads(messages):
    return [item['payload'] for message in messages for attachment in message.get('attachments', [])
            for row in attachment['payload']['buttons'] for item in row]


class StudentReportTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(self.store, today=lambda: date(2026, 11, 8))
        self.send(1, 'enroll:head', True)
        self.send(1, '9А')
        self.head = self.store.member(1)
        for uid in (2, 3):
            self.join(uid, self.head['student_code'])
        self.join(4, self.head['teacher_code'])

    def send(self, uid, text, callback=False):
        return self.engine.handle(uid, text, is_callback=callback)

    def join(self, uid, code):
        self.send(uid, 'enroll:student' if code == self.head['student_code'] else 'join', True)
        self.send(uid, code)

    def publish(self, body):
        preview = self.send(1, '/agent Добавь 9А задание по алгебре к завтра на 25 минут: ' + body)
        confirm = next(value for value in payloads(preview) if value.startswith('confirm:'))
        self.send(1, confirm, True)
        return next(task for task in self.store.tasks(self.head['class_id']) if task['body'] == body)

    def test_roster_counts_members_even_when_their_active_class_is_different(self):
        older = self.publish('Решить № 12')
        latest = self.publish('Решить № 14')
        self.send(2, 'done:' + latest['id'], True)
        self.send(3, 'done:' + older['id'], True)
        self.send(3, 'enroll:head', True)
        self.send(3, '8Б')

        manage = self.send(1, '/class')
        self.assertIn('students:0', payloads(manage))
        roster = self.send(1, 'students:0', True)[0]
        self.assertIn('Ученики — 2. Задания — 2.', roster['text'])
        self.assertIn('Последнее задание · Алгебра · Решить № 14', roster['text'])
        self.assertIn('Выполнили — 1. Без отметки — 1.', roster['text'])
        self.assertEqual(roster['_visual']['metrics'], [('УЧЕНИКОВ', 2), ('ЗАДАНИЙ', 2)])
        report = self.send(1, '/agent Покажи отчёт по заданиям 9А')[0]['text']
        self.assertEqual(report.count('Выполнили — 1. Без отметки — 1.'), 2)

    def test_student_card_filters_tasks_and_renames_display_name(self):
        completed = self.publish('Решить № 12')
        self.publish('Решить № 14')
        self.send(2, 'done:' + completed['id'], True)
        roster = self.send(1, 'students:0', True)
        student = next(value for value in payloads(roster) if value.startswith('student_report:'))
        card = self.send(1, student, True)
        self.assertIn('Ученик 1 · 9А', card[0]['text'])
        self.assertIn('Решить № 12', card[0]['text'])
        self.assertNotIn('Решить № 14', card[0]['text'])
        pending = next(value for value in payloads(card) if ':pending:' in value)
        self.assertIn('Решить № 14', self.send(1, pending, True)[0]['text'])
        rename = next(value for value in payloads(card) if value.startswith('student_name:'))
        self.send(1, rename, True)
        self.assertIn('Введите имя', self.send(1, '123')[0]['text'])
        self.assertIn('Анна П. · 9А', self.send(1, 'Анна   П.')[0]['text'])
        self.assertIn('Анна П.', self.send(1, 'students:0', True)[0]['text'])

    def test_reports_and_edits_are_restricted_to_the_class_head(self):
        roster = self.send(1, 'students:0', True)
        student = next(value for value in payloads(roster) if value.startswith('student_report:'))
        rename = next(value for value in payloads(self.send(1, student, True)) if value.startswith('student_name:'))
        for uid in (2, 4):
            for action in ('students:0', 'student_tasks:0', student, rename):
                self.assertIn('недоступно', self.send(uid, action, True)[0]['text'])
        self.send(1, 'enroll:head', True)
        self.send(1, '8Б')
        self.assertIn('не найден', self.send(1, student, True)[0]['text'])
        self.assertIn('не найден', self.send(1, rename, True)[0]['text'])
