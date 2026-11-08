import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import run_demo
from mera import Engine, Store


def controls(messages):
    return {button['payload'] for attachment in messages[0].get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row}


class ClassroomTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(self.store)

    def click(self, uid, payload):
        return self.engine.handle(uid, payload, is_callback=True)

    def enter(self, uid, text):
        return self.engine.handle(uid, text, is_callback=False)

    def create_class(self, uid=1, grade=9):
        self.click(uid, 'enroll:head')
        self.click(uid, f'class_grade:{grade}')
        self.click(uid, f'class_pick:{grade}:А')
        return self.store.member(uid)

    def test_role_menu_head_teacher_student_and_distinct_invites(self):
        self.assertEqual(controls(self.enter(1, '/start')), {'enroll:head', 'enroll:teacher', 'enroll:student', 'guide'})
        head = self.create_class()
        self.assertEqual(head['name'], '9А')
        self.assertNotEqual(head['teacher_code'], head['student_code'])
        self.click(2, 'enroll:teacher')
        self.click(2, 'catalog:custom')
        self.enter(2, 'Робототехника')
        for invalid in ('INVALID', head['student_code']):
            response = self.enter(2, invalid)
            self.assertIn('Робототехника', response[0]['text'])
            self.assertIsNone(self.store.member(2))
            self.assertEqual(self.store.class_subjects(head['class_id']), [])
        profile = self.enter(2, head['teacher_code'])
        self.assertIn('Мой предмет — Робототехника', profile[0]['text'])
        self.assertIn('subject:Робототехника', controls(self.click(2, 'add')))
        self.click(3, 'enroll:student')
        self.assertIn('другой роли', self.enter(3, head['teacher_code'])[0]['text'])
        plan = self.enter(3, head['student_code'])
        self.assertIn('profile', controls(plan))
        self.assertEqual(self.store.member(3)['role'], 'student')
        self.assertIn('Робототехника · учителей: 1', self.click(1, 'class_subjects:0')[0]['text'])

    def test_catalogue_pagination_covers_three_school_levels(self):
        self.click(2, 'enroll:teacher')
        for group, expected in (
            ('primary', ('Литературное чтение', 'Окружающий мир', 'ОРКСЭ')),
            ('middle', ('Вероятность и статистика', 'История нашего края', 'Второй иностранный язык', 'ОБЗР')),
            ('upper', ('Обществознание', 'Индивидуальный проект')),
        ):
            page = 0
            seen = set()
            while True:
                options = controls(self.click(2, f'catalog:{group}:{page}'))
                seen.update(options)
                page += 1
                if f'catalog:{group}:{page}' not in options:
                    break
            for name in expected:
                self.assertIn(f'subject:{name}', seen)
        chosen = self.click(2, 'subject:Индивидуальный проект')
        self.assertIn('Введите код для учителя', chosen[0]['text'])

    def test_custom_courses_are_deduplicated_and_class_scoped(self):
        head = self.create_class()
        other = self.create_class(uid=9, grade=8)
        self.click(2, 'enroll:teacher')
        self.enter(2, 'Проектная   мастерская')
        self.enter(2, head['teacher_code'])
        self.click(1, 'class_subject_add')
        self.enter(1, 'проектная мастерская')
        self.enter(1, '/agent Добавь предмет «Робототехника» для 9А')
        denied = self.enter(1, '/agent Добавь предмет Биотехнологии в 8А')
        self.assertIn('доступен класс 9А', denied[0]['text'])
        self.assertEqual([row['name'] for row in self.store.class_subjects(head['class_id'])],
                         ['Проектная мастерская', 'Робототехника'])
        self.assertEqual(self.store.class_subjects(other['class_id']), [])
        self.click(3, 'enroll:student')
        self.enter(3, head['student_code'])
        self.assertNotIn('class_subject_add', controls(self.click(3, 'class_subjects:0')))
        self.assertIn('недоступно', self.enter(3, '/agent Добавь предмет Биотехнологии')[0]['text'])

    def test_legacy_teacher_can_choose_subject_from_profile(self):
        head = self.create_class()
        self.click(2, 'join')
        self.enter(2, head['teacher_code'])
        self.assertIn('profile_subject', controls(self.enter(2, '/profile')))
        self.click(2, 'profile_subject')
        self.click(2, 'subject:География')
        self.assertEqual(self.store.member(2)['subject'], 'География')

    def test_legacy_subject_backfill_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'legacy.sqlite'
            store = Store(path)
            engine = Engine(store)
            engine.handle(1, 'create')
            engine.handle(1, '9А')
            with store.db:
                store.db.execute("DELETE FROM metadata WHERE key='class_subjects_v1'")
                store.db.executemany('INSERT INTO tasks VALUES (?,1,1,?,?,?,30)', (
                    ('1', 'География', '№ 1', '2026-11-20'),
                    ('2', 'география', '№ 2', '2026-11-20'),
                ))
            store.close()
            for _ in range(2):
                reopened = Store(path)
                try:
                    self.assertEqual(len(reopened.class_subjects(1)), 1)
                    self.assertEqual(len(reopened.tasks(1)), 2)
                finally:
                    reopened.close()

    def test_demo_reset_requires_confirmation_and_starts_with_role_picker(self):
        steps = ('/1', '9А', '/reset_demo', '/2', '/profile', '/reset_demo', '/1', '/profile', '/quit')
        stream = io.StringIO()
        with patch('builtins.input', side_effect=steps), redirect_stdout(stream):
            run_demo()
        before, after = stream.getvalue().split('Демо сброшено.')
        self.assertIn('Личный кабинет · 9А', before)
        self.assertIn('выберите свою роль', after)
        self.assertNotIn('Личный кабинет · 9А', after)
