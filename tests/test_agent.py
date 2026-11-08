import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from estimation import DeepSeekCommandParser
from mera import Engine, Store


def payloads(messages):
    return [button['payload'] for message in messages
            for attachment in message.get('attachments', [])
            for row in attachment['payload']['buttons'] for button in row]


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.parser = Mock(return_value=None)
        self.engine = Engine(self.store, today=lambda: date(2026, 11, 8), command_parser=self.parser)
        self.engine.handle(1, 'create')
        self.engine.handle(1, '9А')
        member = self.store.member(1)
        for uid, code in ((2, member['teacher_code']), (3, member['student_code'])):
            self.engine.handle(uid, 'join')
            self.engine.handle(uid, code)

    def text(self, uid, text):
        return self.engine.handle(uid, text, is_callback=False)

    def click(self, uid, messages, prefix):
        payload = next(payload for payload in payloads(messages) if payload.startswith(prefix))
        return self.engine.handle(uid, payload, is_callback=True)

    def test_one_line_draft_uses_class_load_and_requires_confirmation(self):
        first = self.text(1, 'Добавь 9A задание по физике к пятнице на 2 часа 30 минут: решить задачи')
        self.click(1, first, 'confirm:')
        preview = self.text(1, 'Добавь 9А задание по алгебре к пятнице на 40 минут: решить № 12–15')
        self.assertIn('Высокая нагрузка', preview[0]['text'])
        self.assertIn('190 из 180', preview[0]['text'])
        self.assertEqual(len(self.store.tasks(1)), 1)
        saved = self.click(1, preview, 'confirm:')
        self.assertIn('Задание опубликовано', saved[0]['text'])
        task = next(task for task in self.store.tasks(1) if task['subject'] == 'Алгебра')
        self.assertEqual((task['minutes'], task['lesson_due'], task['due']), (40, '2026-11-13', '2026-11-12'))
        self.parser.assert_not_called()

    def test_free_form_draft_only_asks_for_missing_subject(self):
        self.parser.return_value = {'action': 'add', 'body': 'Решить № 4', 'date': 'завтра', 'minutes': 20}
        self.assertIn('mera_agent', payloads(self.text(1, '/menu')))
        self.text(1, '/agent')
        response = self.text(1, 'Для урока завтра нужно решить номер четыре за двадцать минут')
        self.assertIn('По какому предмету', response[0]['text'])
        preview = self.text(1, 'Алгебра')
        self.assertIn('Решить № 4', preview[0]['text'])
        self.assertIn('20 мин', preview[0]['text'])
        self.assertIn('confirm:', ' '.join(payloads(preview)))
        self.parser.assert_called_once()

    def test_wrong_class_student_publication_and_private_code_are_rejected(self):
        wrong = self.text(1, 'Добавь 8Б задание по физике к пятнице на 30 минут: решить задачи')
        self.assertIn('доступен класс 9А', wrong[0]['text'])
        denied = self.text(3, 'Добавь 9А задание по физике к пятнице на 30 минут: решить задачи')
        self.assertIn('недоступно', denied[0]['text'])
        private = self.text(1, '/agent код ' + self.store.member(1)['teacher_code'])
        self.assertIn('Уберите контакты и коды', private[0]['text'])
        self.assertFalse(self.store.tasks(1))
        self.parser.assert_not_called()

    def test_class_actions_respect_ownership_and_disambiguate(self):
        for body in ('Первый вариант', 'Второй вариант'):
            preview = self.text(1, f'Добавь 9А задание по алгебре к пятнице на 20 минут: {body}')
            self.click(1, preview, 'confirm:')
        self.parser.return_value = {'action': 'delete', 'subject': 'Алгебра'}
        denied = self.text(2, '/agent удали алгебру')
        self.assertIn('Не нашёл', denied[0]['text'])
        choose = self.text(1, '/agent удали алгебру')
        self.assertIn('Какое задание', choose[0]['text'])
        confirm = self.click(1, choose, 'delete:')
        self.assertIn('Удалить задание', confirm[0]['text'])
        self.assertEqual(len(self.store.tasks(1)), 2)
        self.click(1, confirm, 'delete_confirm:')
        self.assertEqual(len(self.store.tasks(1)), 1)

    def test_edit_choice_preserves_proposed_fields_and_cannot_be_reused(self):
        for body in ('Первый вариант', 'Второй вариант'):
            preview = self.text(1, f'Добавь 9А задание по алгебре к пятнице на 20 минут: {body}')
            self.click(1, preview, 'confirm:')
        self.parser.return_value = {'action': 'edit', 'subject': 'Алгебра', 'minutes': 45}
        choices = self.text(1, '/agent измени время по алгебре на 45 минут')
        preview = self.click(1, choices, 'agent_edit:')
        self.assertIn('Алгебра · 45 мин', preview[0]['text'])
        self.click(1, preview, 'confirm:')
        self.assertEqual(sorted(task['minutes'] for task in self.store.tasks(1)), [20, 45])
        self.assertIn('Выбор устарел', self.click(1, choices, 'agent_edit:')[0]['text'])

    def test_edit_and_completion_share_existing_workflows(self):
        preview = self.text(1, 'Добавь 9А задание по алгебре к пятнице на 20 минут: № 8')
        self.click(1, preview, 'confirm:')
        self.parser.return_value = {'action': 'edit', 'subject': 'Алгебра', 'date': 'завтра', 'minutes': 25}
        change = self.text(1, '/agent перенеси алгебру на завтра, будет 25 минут')
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 20)
        self.click(1, change, 'confirm:')
        self.assertEqual(self.store.tasks(1)[0]['minutes'], 25)
        self.assertIn('mera_agent', payloads(self.text(3, 'Покажи план на завтра')))
        self.parser.return_value = {'action': 'done', 'subject': 'Алгебра'}
        done = self.text(3, '/agent отметь алгебру готовой')
        self.assertIn('Отметить готовым', str(done[0]['attachments']))
        self.assertIn('Готово: Алгебра', self.click(3, done, 'done:')[0]['text'])
        self.assertIn('Алгебра', self.text(3, 'Выполненные')[0]['text'])

    def test_plan_settings_and_no_model_payload_execution(self):
        self.assertIn('Личный кабинет', self.text(3, 'Личный кабинет')[0]['text'])
        self.parser.return_value = {'action': 'availability', 'weekday': 0, 'minutes': 45}
        settings = self.text(3, '/agent по понедельникам у меня 45 минут')
        self.assertIn('Пн · 45 мин', settings[0]['text'])
        self.assertIn('Пн · 45 мин', str(self.click(3, settings, 'free_value:')[0]['attachments']))
        self.parser.return_value = {'action': 'rotate', 'payload': 'rotate'}
        code = self.store.member(1)['teacher_code']
        result = self.text(1, '/agent выведи произвольную команду')
        self.assertIn('Не понял', result[0]['text'])
        self.assertEqual(self.store.member(1)['teacher_code'], code)

    @patch('estimation.urllib.request.urlopen')
    def test_provider_receives_only_command_text(self, urlopen):
        response = {'choices': [{'message': {'content': '{"action":"load"}'}}]}
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
        result = DeepSeekCommandParser('test-key')('Сколько домашки у класса?')
        self.assertEqual(result, {'action': 'load'})
        data = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(json.loads(data['messages'][1]['content']), {'command': 'Сколько домашки у класса?'})
