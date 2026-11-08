"""МЕРА: предметная логика, SQLite и сценарии диалога. Только стандартная библиотека."""
import json
import secrets
import sqlite3
import statistics
import calendar
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from subjects import subject_key


WEEKDAYS = ('Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс')
ROLE_LABELS = {'admin': 'классный руководитель', 'teacher': 'учитель', 'student': 'ученик'}


def class_key(value):
    return re.sub(r'[\s\-–«»"]+', '', value.casefold()).translate(
        str.maketrans('abcehkmoptxy', 'авсенкмортху'))
MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
MONTH_TITLES = ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')


def pretty_date(value):
    day = date.fromisoformat(value) if isinstance(value, str) else value
    return f'{day.day} {MONTHS[day.month - 1]}, {WEEKDAYS[day.weekday()]}'


def moscow_today():
    try:
        tz = ZoneInfo('Europe/Moscow')
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=3))
    return datetime.now(tz).date()


def button(label, value):
    return {'type': 'callback', 'text': label, 'payload': value}


def reply(text, rows=None):
    result = {'text': text}
    if rows:
        result['attachments'] = [{'type': 'inline_keyboard', 'payload': {'buttons': rows}}]
    return result


def parse_date(value, today):
    value = value.strip().lower()
    if value in ('сегодня', 'завтра', 'послезавтра'):
        return today + timedelta(days=('сегодня', 'завтра', 'послезавтра').index(value))
    week_names = ('понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье')
    normalized = value.removeprefix('в ').removeprefix('во ')
    normalized = {'среду': 'среда', 'пятницу': 'пятница', 'субботу': 'суббота'}.get(normalized, normalized)
    if normalized in week_names:
        result = today + timedelta(days=(week_names.index(normalized) - today.weekday()) % 7)
    elif re.fullmatch(r'\d{1,2}[./]\d{1,2}', value):
        day, month = map(int, re.split(r'[./]', value))
        result = date(today.year, month, day)
        if result < today:
            result = date(today.year + 1, month, day)
    elif re.fullmatch(r'\d{1,2}\s+[а-яё]+', value):
        day, month = value.split()
        result = date(today.year, MONTHS.index(month) + 1, int(day))
        if result < today:
            result = date(today.year + 1, MONTHS.index(month) + 1, int(day))
    else:
        try:
            result = date.fromisoformat(value)
        except ValueError:
            result = datetime.strptime(value, '%d.%m.%Y').date()
    if not today <= result <= today + timedelta(days=180):
        raise ValueError('Срок должен быть от сегодняшнего дня до 180 дней вперёд.')
    return result


def bounded_int(value, low, high):
    try:
        result = int(value)
    except (ValueError, TypeError):
        raise ValueError(f'Введите целое число от {low} до {high}.') from None
    if not low <= result <= high:
        raise ValueError(f'Введите целое число от {low} до {high}.')
    return result


class Store:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS classes (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, daily_limit INTEGER NOT NULL,
                student_code TEXT UNIQUE NOT NULL, teacher_code TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS members (
                user_id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
                role TEXT NOT NULL CHECK(role IN ('admin','teacher','student'))
            );
            CREATE TABLE IF NOT EXISTS class_subjects (
                id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
                name TEXT NOT NULL, name_key TEXT NOT NULL, UNIQUE(class_id,name_key)
            );
            CREATE TABLE IF NOT EXISTS teacher_subjects (
                user_id INTEGER PRIMARY KEY REFERENCES members(user_id) ON DELETE CASCADE,
                subject_id INTEGER NOT NULL REFERENCES class_subjects(id)
            );
            CREATE TABLE IF NOT EXISTS class_memberships (
                user_id INTEGER NOT NULL, class_id INTEGER NOT NULL REFERENCES classes(id),
                role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
                subject_id INTEGER REFERENCES class_subjects(id), PRIMARY KEY(user_id,class_id)
            );
            CREATE INDEX IF NOT EXISTS class_memberships_class ON class_memberships(class_id,role);
            CREATE TABLE IF NOT EXISTS class_teacher_defaults (
                user_id INTEGER NOT NULL, class_id INTEGER NOT NULL REFERENCES classes(id),
                subject TEXT NOT NULL, body TEXT NOT NULL, minutes INTEGER NOT NULL,
                PRIMARY KEY(user_id,class_id)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
                creator INTEGER NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                due TEXT NOT NULL, minutes INTEGER NOT NULL CHECK(minutes BETWEEN 1 AND 600)
            );
            CREATE INDEX IF NOT EXISTS tasks_class_due ON tasks(class_id,due);
            CREATE TABLE IF NOT EXISTS done (
                user_id INTEGER NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                actual INTEGER, PRIMARY KEY(user_id,task_id)
            );
            CREATE TABLE IF NOT EXISTS availability (
                user_id INTEGER PRIMARY KEY, minutes TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS states (user_id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processed (event_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, value TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS screens (user_id INTEGER PRIMARY KEY, mid TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cleanup (mid TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS media_cache (hash TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS task_details (
                task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                lesson_due TEXT, revision INTEGER NOT NULL DEFAULT 0, updated_on TEXT
            );
            CREATE TABLE IF NOT EXISTS study_log (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
                day TEXT NOT NULL, worked INTEGER NOT NULL CHECK(worked>=0),
                credited INTEGER NOT NULL CHECK(credited>=0), token TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS study_user_day ON study_log(user_id,day);
            CREATE TABLE IF NOT EXISTS completion_info (
                user_id INTEGER NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                day TEXT NOT NULL, log_id INTEGER REFERENCES study_log(id) ON DELETE SET NULL,
                PRIMARY KEY(user_id,task_id)
            );
            CREATE TABLE IF NOT EXISTS teacher_defaults (
                user_id INTEGER PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL, minutes INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS day_capacity (
                user_id INTEGER NOT NULL, day TEXT NOT NULL, minutes INTEGER NOT NULL,
                PRIMARY KEY(user_id,day)
            );
        ''')
        self.db.execute('''INSERT OR IGNORE INTO teacher_defaults(user_id,subject,body,minutes)
            SELECT t.creator,t.subject,t.body,t.minutes FROM tasks t
            WHERE t.rowid=(SELECT MAX(recent.rowid) FROM tasks recent WHERE recent.creator=t.creator)''')
        self.db.execute('''INSERT OR IGNORE INTO class_memberships(user_id,class_id,role,subject_id)
            SELECT m.user_id,m.class_id,m.role,s.id FROM members m
            LEFT JOIN teacher_subjects p ON p.user_id=m.user_id
            LEFT JOIN class_subjects s ON s.id=p.subject_id AND s.class_id=m.class_id''')
        self.db.execute('''INSERT OR IGNORE INTO class_teacher_defaults(user_id,class_id,subject,body,minutes)
            SELECT t.creator,t.class_id,t.subject,t.body,t.minutes FROM tasks t
            WHERE t.rowid=(SELECT MAX(recent.rowid) FROM tasks recent
                           WHERE recent.creator=t.creator AND recent.class_id=t.class_id)''')
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='class_memberships_v1'").fetchone():
            self.db.execute('''INSERT OR IGNORE INTO class_teacher_defaults
                SELECT m.user_id,m.class_id,d.subject,d.body,d.minutes FROM members m
                JOIN teacher_defaults d ON d.user_id=m.user_id''')
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('class_memberships_v1','1')")
        if not self.db.execute("SELECT 1 FROM metadata WHERE key='class_subjects_v1'").fetchone():
            with self.db:
                for row in self.db.execute('SELECT DISTINCT class_id,subject FROM tasks'):
                    self.ensure_class_subject(row['class_id'], row['subject'])
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('class_subjects_v1','1')")
        self.db.commit()

    def member(self, uid):
        return self.db.execute('''SELECT m.*,c.name,c.daily_limit,c.student_code,c.teacher_code,s.name AS subject
            FROM members m JOIN classes c ON c.id=m.class_id
            LEFT JOIN class_memberships p ON p.user_id=m.user_id AND p.class_id=m.class_id
            LEFT JOIN class_subjects s ON s.id=p.subject_id AND s.class_id=m.class_id
            WHERE m.user_id=?''', (uid,)).fetchone()

    def memberships(self, uid):
        return self.db.execute('''SELECT m.class_id,m.role,c.name,c.daily_limit,s.name AS subject
            FROM class_memberships m JOIN classes c ON c.id=m.class_id
            LEFT JOIN class_subjects s ON s.id=m.subject_id AND s.class_id=m.class_id
            WHERE m.user_id=? ORDER BY c.name COLLATE NOCASE,m.class_id''', (uid,)).fetchall()

    def activate_class(self, uid, class_id):
        row = self.db.execute('SELECT role FROM class_memberships WHERE user_id=? AND class_id=?',
                              (uid, class_id)).fetchone()
        if not row:
            raise ValueError('Этот класс недоступен для вашего аккаунта.')
        self.db.execute('''INSERT INTO members VALUES (?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET class_id=excluded.class_id,role=excluded.role''',
            (uid, class_id, row['role']))
        self.set_state(uid, {})
        return self.member(uid)

    def enroll(self, uid, class_id, role):
        self.db.execute('INSERT OR IGNORE INTO class_memberships(user_id,class_id,role) VALUES (?,?,?)',
                        (uid, class_id, role))
        return self.activate_class(uid, class_id)

    def teacher_default(self, uid, class_id):
        return self.db.execute('SELECT * FROM class_teacher_defaults WHERE user_id=? AND class_id=?',
                               (uid, class_id)).fetchone()

    def invitation(self, code):
        code = code.upper()
        return self.db.execute('''SELECT id,name,CASE WHEN teacher_code=? THEN 'teacher' ELSE 'student' END AS role
            FROM classes WHERE student_code=? OR teacher_code=?''', (code, code, code)).fetchone()

    def ensure_class_subject(self, class_id, name):
        key = subject_key(name)
        self.db.execute('''INSERT INTO class_subjects(class_id,name,name_key) VALUES (?,?,?)
            ON CONFLICT(class_id,name_key) DO NOTHING''', (class_id, ' '.join(name.split()), key))
        return self.db.execute('SELECT id FROM class_subjects WHERE class_id=? AND name_key=?',
                               (class_id, key)).fetchone()['id']

    def set_teacher_subject(self, uid, class_id, name):
        subject_id = self.ensure_class_subject(class_id, name)
        self.db.execute('UPDATE class_memberships SET subject_id=? WHERE user_id=? AND class_id=? AND role=?',
                        (subject_id, uid, class_id, 'teacher'))

    def class_subjects(self, class_id):
        rows = self.db.execute('''SELECT s.name,COUNT(p.user_id) AS teachers FROM class_subjects s
            LEFT JOIN class_memberships p ON p.subject_id=s.id AND p.class_id=s.class_id AND p.role='teacher'
            WHERE s.class_id=? GROUP BY s.id''', (class_id,))
        return sorted(rows, key=lambda row: subject_key(row['name']))

    def state(self, uid):
        row = self.db.execute('SELECT value FROM states WHERE user_id=?', (uid,)).fetchone()
        return json.loads(row['value']) if row else {}

    def set_state(self, uid, value):
        self.db.execute('INSERT OR REPLACE INTO states VALUES (?,?)', (uid, json.dumps(value)))

    def tasks(self, class_id):
        return [dict(r) for r in self.db.execute(
            '''SELECT t.*,d.lesson_due,COALESCE(d.revision,0) AS revision,d.updated_on FROM tasks t
               LEFT JOIN task_details d ON d.task_id=t.id WHERE t.class_id=? ORDER BY t.due,t.subject,t.id''', (class_id,))]

    def close(self):
        self.db.close()


def build_plan(tasks, done_ids, available, today, days=7, progress=None, spent=None, overrides=None):
    """Распределение по ближайшему сроку; после дедлайна работа не назначается."""
    dates = [today + timedelta(days=i) for i in range(days)]
    plan = {d.isoformat(): [] for d in dates}
    progress,spent,overrides=progress or {},spent or {},overrides or {}
    remaining_capacity = {d.isoformat(): max(0,overrides.get(d.isoformat(),available[d.weekday()])-spent.get(d.isoformat(),0)) for d in dates}
    overdue, overflow = [], []
    for task in sorted(tasks, key=lambda t: (t['due'], t['id'])):
        if task['id'] in done_ids:
            continue
        minutes = max(0,task['minutes']-progress.get(task['id'],0))
        if task['due'] < today.isoformat():
            overdue.append({**task,'minutes':minutes})
            continue
        if not minutes:
            plan[today.isoformat()].append({'task':task,'minutes':0})
            continue
        for day in dates:
            key = day.isoformat()
            if key > task['due']:
                break
            take = min(minutes, remaining_capacity[key])
            if take:
                plan[key].append({'task': task, 'minutes': take})
                remaining_capacity[key] -= take
                minutes -= take
            if not minutes:
                break
        if minutes:
            overflow.append({'task': task, 'minutes': minutes})
    return plan, overdue, overflow


class LegacyEngine:
    def __init__(self, store, today=moscow_today, estimator=None, command_parser=None):
        self.store, self.db, self.today = store, store.db, today
        self.estimator = estimator
        self.command_parser = command_parser

    def handle(self, uid, text, event_id=None, source_mid=None, is_callback=None):
        """Данные, состояние диалога и очередь ответов фиксируются одной транзакцией."""
        uid = int(uid)
        with self.db:
            if event_id and self.db.execute('SELECT 1 FROM processed WHERE event_id=?', (event_id,)).fetchone():
                return []
            try:
                messages = self.route_input(uid, str(text).strip(), is_callback)
            except ValueError as exc:
                messages = [reply(str(exc), [[button('Отмена', 'menu')]])]
            for message in messages:
                if '_visual' not in message:
                    message['_visual'] = self.visual(uid, text, message)
                if source_mid:
                    message['_source_mid'] = source_mid
            if event_id:
                for message in messages:
                    self.db.execute('INSERT INTO outbox(user_id,value) VALUES (?,?)',
                                    (uid, json.dumps(message, ensure_ascii=False)))
                self.db.execute('INSERT INTO processed VALUES (?)', (event_id,))
            return messages

    def route_input(self, uid, text, is_callback):
        return self._handle(uid, text)

    def menu(self, uid, prefix=''):
        member = self.store.member(uid)
        if not member:
            return [reply(prefix + 'МЕРА · радар учебной нагрузки\n\n'
                'Учителю — общая нагрузка класса до публикации задания.\n'
                'Ученику — личный план по доступному времени.\n\n'
                'Администратор создаёт класс и выдаёт разные коды ученикам и учителям. '
                'Храним ваш ID в MAX, задания, настройки времени и отметки выполнения. '
                'Фамилии для работы не нужны.',
                [[button('Создать класс', 'create')], [button('Войти по коду', 'join')]])]
        role = ROLE_LABELS[member['role']]
        rows = [[button('Нагрузка класса', 'load')]]
        if member['role'] == 'student':
            rows.insert(0, [button('Мой план', 'plan'), button('Моё время', 'free')])
        else:
            rows.insert(0, [button('Добавить задание', 'add')])
            rows.append([button('Задания и статистика', 'tasks')])
        if member['role'] == 'admin':
            rows.append([button('Управление классом', 'manage')])
        return [reply(prefix + f'МЕРА · {member["name"]}\nВы — {role}.\n\n'
                      '/menu — вернуться сюда, /cancel — отменить ввод.\n\nВыберите действие:', rows)]

    def require(self, uid, roles=None):
        member = self.store.member(uid)
        if not member:
            raise ValueError('Сначала создайте класс или войдите по коду: /menu')
        if roles and member['role'] not in roles:
            raise ValueError('Это действие недоступно для вашей роли.')
        return member

    def _handle(self, uid, text):
        aliases = {'/start': 'menu', '/menu': 'menu', '/cancel': 'menu', '/help': 'help',
                   '/plan': 'plan', '/load': 'load', '/add': 'add', '/class': 'manage',
                   '/tasks': 'tasks', '/id': 'id'}
        text = aliases.get(text, text)
        if text == 'id':
            return [reply(f'Ваш ID в MAX: {uid}')]
        if text == 'help':
            return [reply('МЕРА помогает распределять домашнюю работу.\n\n'
                'Срок означает «выполнить до конца указанного дня». Если задание нужно '
                'к утреннему уроку, укажите предыдущий день. Оценку минут вводит учитель.\n\n'
                'Нагрузка класса — сумма заданий по срокам, без личных отметок. '
                'Мой план — распределение невыполненных заданий с учётом свободного времени. '
                'Большое задание может быть разбито на части. Отмечайте его готовым целиком.\n\n'
                'Порог класса задаёт администратор; это настройка проекта, а не медицинская норма. '
                'Если времени не хватает, бот сообщает об этом и не переносит дедлайн сам.',
                [[button('Главное меню', 'menu')]])]
        if text == 'menu':
            self.store.set_state(uid, {})
            return self.menu(uid)
        if text in ('create', 'join'):
            if self.store.member(uid):
                return self.menu(uid, 'Вы уже подключены к классу.\n\n')
            self.store.set_state(uid, {'step': text})
            return [reply('Название класса, например 9А:' if text == 'create' else 'Введите код от администратора класса:',
                          [[button('Отмена', 'menu')]])]
        if text == 'load':
            return self.load(uid)
        if text == 'plan':
            self.require(uid, ('student',))
            return self.plan(uid)
        if text.startswith('plan:'):
            parts = text.split(':')
            if len(parts) != 3:
                raise ValueError('Откройте план заново через меню.')
            return self.plan(uid, bounded_int(parts[1], -1, 6), bounded_int(parts[2], 0, 999))
        if text == 'free':
            return self.free_time(uid)
        if text.startswith('free_day:'):
            self.require(uid, ('student',))
            day = bounded_int(text.split(':')[1], 0, 6)
            self.store.set_state(uid, {'step': 'free_day', 'day': day})
            return [reply(f'{WEEKDAYS[day]} · сколько времени выделим на учёбу?',
                [[button(f'{n} мин', f'free_value:{day}:{n}') for n in pair] for pair in ((30,60),(90,120),(150,180))] +
                [[button('Выходной', f'free_value:{day}:0')], [button('Назад', 'free')]])]
        if text.startswith('free_value:'):
            self.require(uid, ('student',))
            parts = text.split(':')
            if len(parts) != 3:
                raise ValueError('Откройте настройки времени заново.')
            day, minutes = bounded_int(parts[1], 0, 6), bounded_int(parts[2], 0, 600)
            row = self.db.execute('SELECT minutes FROM availability WHERE user_id=?', (uid,)).fetchone()
            values = json.loads(row['minutes']) if row else [120] * 7
            values[day] = minutes
            self.db.execute('INSERT OR REPLACE INTO availability VALUES (?,?)', (uid, json.dumps(values)))
            return self.free_time(uid)
        if text == 'manage':
            member = self.require(uid, ('admin',))
            return [reply(f'Управление · {member["name"]}\n\n'
                f'Код для учеников: {member["student_code"]}\n'
                f'Код для учителей: {member["teacher_code"]}\n\n'
                'Передавайте учительский код только учителям.\n'
                f'Порог нагрузки: {member["daily_limit"]} мин/день. Это настройка пилота.',
                [[button('Предметы класса', 'class_subjects:0')],
                 [button('Изменить порог', 'limit')],
                 [button('Обновить коды', f'rotate_class:{member["class_id"]}')],
                 [button('Главное меню', 'menu')]])]
        if text == 'limit':
            self.require(uid, ('admin',))
            self.store.set_state(uid, {'step': 'limit'})
            return [reply('Введите порог нагрузки класса в минутах: от 30 до 600.')]
        if text == 'rotate':
            member = self.require(uid, ('admin',))
            self.db.execute('UPDATE classes SET student_code=?,teacher_code=? WHERE id=?',
                (secrets.token_hex(5).upper(), secrets.token_hex(5).upper(), member['class_id']))
            return self._handle(uid, 'manage')
        if text == 'tasks':
            return self.task_list(uid)
        if text.startswith('tasks:'):
            return self.task_list(uid, bounded_int(text.split(':')[1], 0, 999))
        if text.startswith('done:'):
            member = self.require(uid, ('student',))
            task = self.task(uid, text[5:])
            existing = self.db.execute('SELECT 1 FROM done WHERE user_id=? AND task_id=?', (uid, task['id'])).fetchone()
            if existing:
                return [reply('Это задание уже отмечено выполненным.', [[button('Мой план', 'plan')]])]
            self.db.execute('INSERT INTO done(user_id,task_id) VALUES (?,?)', (uid, task['id']))
            return [reply(f'Готово: {task["subject"]}.\n\nСколько минут ушло на всё задание? '
                'Ответ необязателен; учитель видит только общую медиану от 3 ответов.',
                [[button(f'{n} мин', f'actual:{task["id"]}:{n}') for n in (15, 30, 45)],
                 [button('Другое время', f'actual_custom:{task["id"]}'), button('Пропустить', 'plan')]])]
        if text.startswith('actual_custom:'):
            self.require(uid, ('student',))
            task = self.task(uid, text.split(':')[1])
            self.store.set_state(uid, {'step': 'actual', 'task_id': task['id']})
            return [reply('Сколько минут ушло на всё задание? Введите число от 1 до 600.')]
        if text.startswith('actual:'):
            self.require(uid, ('student',))
            parts = text.split(':')
            if len(parts) != 3:
                raise ValueError('Кнопка устарела. Откройте /menu.')
            self.set_actual(uid, parts[1], bounded_int(parts[2], 1, 600))
            return [reply('Спасибо! Время сохранено.', [[button('Мой план', 'plan')]])]
        if text.startswith('delete:') or text.startswith('delete_confirm:'):
            member = self.require(uid, ('admin', 'teacher'))
            task = self.task(uid, text.split(':')[1])
            if member['role'] != 'admin' and task['creator'] != uid:
                raise ValueError('Учитель может удалять только собственные задания.')
            if text.startswith('delete_confirm:'):
                self.db.execute('DELETE FROM tasks WHERE id=?', (task['id'],))
                return [reply('Задание удалено.', [[button('Задания', 'tasks')]])]
            return [reply(f'Удалить задание «{task["subject"]}: {task["body"]}»? Отметки выполнения тоже будут удалены.',
                [[button('Удалить', f'delete_confirm:{task["id"]}'), button('Отмена', 'tasks')]])]

        state = self.store.state(uid)
        step = state.get('step')
        if text.startswith('subject:'):
            if step != 'subject':
                raise ValueError('Эта кнопка устарела. Начните новое задание.')
            text = text.split(':', 1)[1]
        if text.startswith('minutes:'):
            if step != 'minutes':
                raise ValueError('Эта кнопка устарела. Продолжите добавление задания.')
            text = text.split(':', 1)[1]
        if text.startswith('calendar:'):
            self.require(uid, ('admin', 'teacher'))
            if step != 'due':
                raise ValueError('Календарь устарел. Начните новое задание.')
            try:
                month = date.fromisoformat(text.split(':')[1] + '-01')
            except ValueError:
                raise ValueError('Откройте календарь заново.') from None
            if month < self.today().replace(day=1) or month > self.today() + timedelta(days=180):
                raise ValueError('Выберите месяц в пределах ближайших 180 дней.')
            return self.date_picker(uid, month)
        if text == 'calendar_noop':
            month = state.get('calendar_month')
            return self.date_picker(uid, date.fromisoformat(month) if month else None)
        if text.startswith('due:'):
            if step != 'due':
                raise ValueError('Эта дата относится к старому заданию. Нажмите /add.')
            text = text.split(':', 1)[1]
        if text.startswith(('confirm:', 'move:', 'editminutes:')):
            self.require(uid, ('admin', 'teacher'))
            action, nonce = text.split(':', 1)
            if step != 'confirm' or nonce != state.get('nonce'):
                raise ValueError('Эта кнопка уже использована или устарела. /add — новое задание.')
            if action == 'confirm':
                member = self.require(uid, ('admin', 'teacher'))
                draft = state['draft']
                total = self.db.execute('SELECT COALESCE(SUM(minutes),0) FROM tasks WHERE class_id=? AND due=?',
                                        (member['class_id'], draft['due'])).fetchone()[0] + draft['minutes']
                if total > member['daily_limit'] and total != state.get('preview_total'):
                    result = self.preview(uid, draft)
                    result[0]['text'] = 'Нагрузка изменилась. Проверьте её ещё раз.\n\n' + result[0]['text']
                    return result
                return self.publish(uid, state['draft'])
            state['step'] = 'due' if action == 'move' else 'minutes'
            self.store.set_state(uid, state)
            return self.date_picker(uid) if action == 'move' else self.minute_picker(uid)
        if step == 'create':
            return self.create_class(uid, text)
        if step == 'join':
            row = self.store.invitation(text)
            if not row:
                if state.get('expected_role'):
                    return self.invite_prompt(state, 'Код не найден. Проверьте его и попробуйте ещё раз.')
                raise ValueError('Код не найден. Проверьте его у администратора и попробуйте снова.')
            role = row['role']
            if state.get('expected_role') and role != state['expected_role']:
                return self.invite_prompt(state, 'Этот код для другой роли. Запросите подходящий код у классного руководителя.')
            member = self.store.enroll(uid, row['id'], role)
            if member['role'] == 'teacher' and not member['subject'] and state.get('teacher_subject'):
                self.store.set_teacher_subject(uid, row['id'], state['teacher_subject'])
            self.store.set_state(uid, {})
            if state.get('expected_role') == 'teacher':
                return self.profile(uid, prefix='Вы подключены к классу.\n\n')
            return self.menu(uid, 'Вы подключены!\n\n')
        if step == 'free':
            self.require(uid, ('student',))
            parts = text.split()
            if len(parts) != 7:
                raise ValueError('Нужно 7 чисел: минуты с понедельника по воскресенье.')
            values = [bounded_int(v, 0, 600) for v in parts]
            self.db.execute('INSERT OR REPLACE INTO availability VALUES (?,?)', (uid, json.dumps(values)))
            self.store.set_state(uid, {})
            return self.plan(uid)
        if step == 'limit':
            member = self.require(uid, ('admin',))
            self.db.execute('UPDATE classes SET daily_limit=? WHERE id=?',
                            (bounded_int(text, 30, 600), member['class_id']))
            self.store.set_state(uid, {})
            return self._handle(uid, 'manage')
        if step == 'actual':
            self.require(uid, ('student',))
            self.set_actual(uid, state['task_id'], bounded_int(text, 1, 600))
            self.store.set_state(uid, {})
            return self.plan(uid)
        if step in ('subject', 'body', 'due', 'minutes'):
            self.require(uid, ('admin', 'teacher'))
            draft = state['draft']
            if step in ('subject', 'body'):
                maximum = 60 if step == 'subject' else 700
                if not 1 <= len(text) <= maximum:
                    raise ValueError(f'Введите от 1 до {maximum} символов.')
                draft[step] = text
                state['step'] = 'body' if step == 'subject' else 'due'
                prompt = 'Шаг 2 из 4 · Что нужно сделать?\nНапример: № 8–10, стр. 42.'
            elif step == 'due':
                try:
                    draft['due'] = parse_date(text, self.today()).isoformat()
                except ValueError:
                    return self.date_picker(uid, error='Не распознал дату. Выберите день кнопкой ниже.')
                if 'minutes' in draft:
                    return self.preview(uid, draft)
                state['step'] = 'minutes'
                prompt = 'Сколько минут займёт задание?'
            else:
                draft['minutes'] = bounded_int(text, 1, 600)
                return self.preview(uid, draft)
            self.store.set_state(uid, state)
            if state['step'] == 'due':
                return self.date_picker(uid)
            if state['step'] == 'minutes':
                return self.minute_picker(uid)
            return [reply(prompt, [[button('Отмена', 'menu')]])]
        return self.menu(uid, 'Используйте кнопки или /help.\n\n')

    def create_class(self, uid, name):
        if not 1 <= len(name) <= 40:
            raise ValueError('Название класса: от 1 до 40 символов.')
        if re.fullmatch(r'\d{1,2}\s*[-–]?\s*[а-яa-z]', name, re.IGNORECASE):
            name = class_key(name).upper()
        if any(row['role'] == 'admin' and class_key(row['name']) == class_key(name)
               for row in self.store.memberships(uid)):
            raise ValueError('У вас уже есть такой класс. Откройте «Мои классы».')
        cursor = self.db.execute('INSERT INTO classes(name,daily_limit,student_code,teacher_code) VALUES (?,?,?,?)',
            (name, 180, secrets.token_hex(5).upper(), secrets.token_hex(5).upper()))
        self.store.enroll(uid, cursor.lastrowid, 'admin')
        return self._handle(uid, 'manage')

    def preview(self, uid, draft):
        member = self.require(uid, ('admin', 'teacher'))
        nonce = secrets.token_hex(5)
        total = self.db.execute('SELECT COALESCE(SUM(minutes),0) FROM tasks WHERE class_id=? AND due=?',
                                (member['class_id'], draft['due'])).fetchone()[0] + draft['minutes']
        self.store.set_state(uid, {'step': 'confirm', 'draft': draft, 'nonce': nonce, 'preview_total': total})
        high = total > member['daily_limit']
        message = ('⚠ Высокая нагрузка' if high else 'Проверка задания')
        message += (f' · {member["name"]}\n\n{draft["subject"]}: {draft["body"]}\n'
                    f'Срок: {pretty_date(draft["due"])}, до конца дня\nОценка: {draft["minutes"]} мин\n\n'
                    f'С новым заданием: {total} мин на эту дату.\nПорог класса: {member["daily_limit"]} мин.')
        if high:
            message += f'\nПревышение: {total - member["daily_limit"]} мин.\nМожно изменить срок или объём задания.'
            alternatives = []
            for offset in range(1, 8):
                day = (date.fromisoformat(draft['due']) + timedelta(days=offset)).isoformat()
                load = self.db.execute('SELECT COALESCE(SUM(minutes),0) FROM tasks WHERE class_id=? AND due=?',
                                      (member['class_id'], day)).fetchone()[0]
                if load + draft['minutes'] <= member['daily_limit']:
                    alternatives.append(f'{pretty_date(day)}: {load + draft["minutes"]} мин с заданием')
                if len(alternatives) == 2:
                    break
            if alternatives:
                message += '\n\nМенее загруженные даты (согласуйте с расписанием):\n' + '\n'.join(alternatives)
        return [reply(message, [[button('Оставить как есть' if high else 'Опубликовать', f'confirm:{nonce}')],
            [button('Изменить срок', f'move:{nonce}'), button('Изменить минуты', f'editminutes:{nonce}')],
            [button('Отмена', 'menu')]])]

    def publish(self, uid, draft):
        member = self.require(uid, ('admin', 'teacher'))
        if draft['due'] < self.today().isoformat():
            raise ValueError('Срок уже прошёл. Измените дату перед публикацией.')
        self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
                        (secrets.token_hex(6), member['class_id'], uid, draft['subject'], draft['body'], draft['due'], draft['minutes']))
        self.store.set_state(uid, {})
        return self.menu(uid, 'Задание опубликовано и доступно ученикам в «Мой план».\n\n')

    def task(self, uid, task_id):
        member = self.require(uid)
        task = self.db.execute('SELECT * FROM tasks WHERE id=? AND class_id=?', (task_id, member['class_id'])).fetchone()
        if not task:
            raise ValueError('Задание не найдено в вашем классе или удалено.')
        return task

    def set_actual(self, uid, task_id, actual):
        self.task(uid, task_id)
        cursor = self.db.execute('UPDATE done SET actual=? WHERE user_id=? AND task_id=?', (actual, uid, task_id))
        if not cursor.rowcount:
            raise ValueError('Сначала отметьте задание выполненным.')

    def load(self, uid):
        member = self.require(uid)
        today = self.today()
        lines = [f'Нагрузка · {member["name"]}', f'Порог класса: {member["daily_limit"]} мин/день', '']
        tasks = self.store.tasks(member['class_id'])
        for i in range(7):
            day = today + timedelta(days=i)
            group = [t for t in tasks if t['due'] == day.isoformat()]
            total = sum(t['minutes'] for t in group)
            label = '⚠' if total > member['daily_limit'] else '·'
            bar = '▰' * min(12, (total + 29) // 30) or '—'
            lines.append(f'{label} {WEEKDAYS[day.weekday()]} {day:%d.%m}: {total} мин ({len(group)} зад.) {bar}')
        lines.append('\nСумма по вечерам подготовки. Учитываются только задания в боте; личные отметки не меняют нагрузку класса.')
        return [reply('\n'.join(lines), [[button('Главное меню', 'menu')]])]

    def date_picker(self, uid, month=None, error=''):
        self.require(uid, ('admin', 'teacher'))
        if self.store.state(uid).get('step') != 'due':
            raise ValueError('Сначала начните добавление задания.')
        today = self.today()
        state = self.store.state(uid)
        state['calendar_month'] = month.isoformat() if month else None
        self.store.set_state(uid, state)
        rows = []
        if month is None:
            rows = [[button('Сегодня', 'due:' + today.isoformat()),
                     button('Завтра', 'due:' + (today + timedelta(days=1)).isoformat())]]
            for offset in (2, 4, 6):
                rows.append([button(pretty_date(today + timedelta(days=i)),
                                    'due:' + (today + timedelta(days=i)).isoformat()) for i in (offset, offset + 1)])
            rows.append([button('Календарь месяца', f'calendar:{today:%Y-%m}')])
            caption = '3 из 4 · На какой день задание?\n\nВыберите удобный срок кнопкой.\nДля утреннего урока выбирайте предыдущий день.'
        else:
            caption = f'{MONTH_TITLES[month.month - 1]} {month.year}\n\nНажмите на день. Недоступные даты отмечены точкой.'
            rows.append([button(d, 'calendar_noop') for d in WEEKDAYS])
            for week in calendar.monthcalendar(month.year, month.month):
                line = []
                for number in week:
                    day = month.replace(day=number) if number else None
                    valid = day and today <= day <= today + timedelta(days=180)
                    line.append(button(str(number) if valid else '·', 'due:' + day.isoformat() if valid else 'calendar_noop'))
                rows.append(line)
            nav = []
            previous = (month - timedelta(days=1)).replace(day=1)
            following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            if previous >= today.replace(day=1):
                nav.append(button('‹ ' + MONTH_TITLES[previous.month - 1], f'calendar:{previous:%Y-%m}'))
            if following <= today + timedelta(days=180):
                nav.append(button(MONTH_TITLES[following.month - 1] + ' ›', f'calendar:{following:%Y-%m}'))
            if nav:
                rows.append(nav)
        rows.append([button('Отмена', 'menu')])
        visual = {'kind': 'calendar', 'title': 'ВЫБЕРИ\nДАТУ', 'subtitle': 'Без ввода года, месяца и дня',
                  'step': 3, 'month': (month or today).replace(day=1).isoformat(), 'today': today.isoformat(),
                  'subject': state.get('draft', {}).get('subject', ''),
                  'body': state.get('draft', {}).get('body', ''),
                  'selected_due': state.get('draft', {}).get('due')}
        message = reply((error + '\n\n' if error else '') + caption, rows)
        message['_visual'] = visual
        return [message]

    def minute_picker(self, uid=None):
        return [reply('4 из 4 · Сколько времени займёт задание?\n\nВыберите оценку или напишите своё число минут.',
                      [[button('15 мин', 'minutes:15'), button('30 мин', 'minutes:30')],
                       [button('45 мин', 'minutes:45'), button('60 мин', 'minutes:60')],
                       [button('90 мин', 'minutes:90'), button('120 мин', 'minutes:120')],
                       [button('Отмена', 'menu')]])]

    def free_time(self, uid):
        self.require(uid, ('student',))
        self.store.set_state(uid, {'step': 'free'})
        row = self.db.execute('SELECT minutes FROM availability WHERE user_id=?', (uid,)).fetchone()
        values = json.loads(row['minutes']) if row else [120] * 7
        controls = [button(f'{label} · {value} мин' if value else label + ' · выходной', f'free_day:{i}')
                    for i,(label,value) in enumerate(zip(WEEKDAYS,values))]
        rows = [controls[i:i + 2] for i in range(0,7,2)]
        rows.append([button('Посмотреть план', 'plan'), button('Меню', 'menu')])
        return [reply('Моё время\n\nНажмите день, чтобы изменить минуты. Выходной — тоже часть плана.\n'
                      'По умолчанию заложено 120 минут на день.', rows)]

    def visual(self, uid, action, message):
        state = self.store.state(uid)
        member = self.store.member(uid)
        base = {'kind': 'menu', 'title': 'ГЛАВНОЕ\nМЕНЮ', 'subtitle': 'Всё по плану. Всё в меру.'}
        if member:
            base['class_name'] = member['name']
            base['role'] = ROLE_LABELS[member['role']].capitalize()
            tasks = self.store.tasks(member['class_id'])
            base.update(task_count=len(tasks), limit=member['daily_limit'],
                        tomorrow_minutes=sum(t['minutes'] for t in tasks if t['due'] == (self.today() + timedelta(days=1)).isoformat()))
        else:
            base.update(title='УЧЁБА\nВ МЕРУ', subtitle='Задания, время и спокойный план')
        step = state.get('step')
        if step in ('subject', 'body', 'due', 'minutes'):
            number = ('subject', 'body', 'due', 'minutes').index(step) + 1
            base.update(kind='wizard', title='ДОБАВИТЬ\nЗАДАНИЕ', step=number,
                        subtitle=('Выберите предмет', 'Что нужно сделать?', 'Выберите удобный день', 'Оцените время выполнения')[number - 1],
                        subject=state.get('draft', {}).get('subject', ''),
                        body=state.get('draft', {}).get('body', ''),
                        due=pretty_date(state['draft']['due']) if state.get('draft', {}).get('due') else '',
                        minutes=state.get('draft', {}).get('minutes'))
        elif step == 'confirm':
            draft = state['draft']
            total = state.get('preview_total', draft['minutes'])
            high = member and total > member['daily_limit']
            base.update(kind='warning' if high else 'preview', title='ВЫСОКАЯ\nНАГРУЗКА' if high else 'ПРОВЕРИМ\nЗАДАНИЕ',
                        subtitle='Измените срок или оставьте как есть' if high else 'Один шаг до публикации',
                        total=total, before=total-draft['minutes'], limit=member['daily_limit'], minutes=draft['minutes'],
                        subject=draft['subject'], body=draft['body'], due=pretty_date(draft['due']))
        elif step in ('create', 'join'):
            base.update(kind='manage', title='ВАШ\nКЛАСС' if step == 'create' else 'БУДЕМ\nВ КОМАНДЕ',
                        subtitle='Придумайте название класса' if step == 'create' else 'Введите код от администратора')
        elif step in ('free', 'free_day', 'limit') or action == 'free':
            base.update(kind='time', title='УЧЁБА\nВ СВОЁМ ТЕМПЕ', subtitle='Настройте время под свой день')
            if step != 'limit':
                row = self.db.execute('SELECT minutes FROM availability WHERE user_id=?', (uid,)).fetchone()
                values = json.loads(row['minutes']) if row else [120] * 7
                base.update(bars=values, labels=list(WEEKDAYS), selected=state.get('day'),
                            total=sum(values), limit=0, daily_limit=member['daily_limit'] if member else 0)
            else:
                base.update(title='ПОРОГ\nНАГРУЗКИ', subtitle='Текущая настройка класса', current_limit=member['daily_limit'])
        if action in ('load', '/load') and member:
            totals = [sum(t['minutes'] for t in self.store.tasks(member['class_id'])
                          if t['due'] == (self.today() + timedelta(days=i)).isoformat()) for i in range(7)]
            base.update(kind='load', title='НАГРУЗКА\nКЛАССА', subtitle='Видно всю неделю, а не один предмет',
                        bars=totals, limit=member['daily_limit'],
                        labels=[WEEKDAYS[(self.today() + timedelta(days=i)).weekday()] for i in range(7)])
        elif action in ('manage', '/class', 'rotate') or message['text'].startswith('Управление ·'):
            base.update(kind='manage', title='УПРАВЛЕНИЕ\nКЛАССОМ', subtitle='Учителя и ученики — одна команда')
            if member:
                base['role_counts'] = {role: self.db.execute('SELECT COUNT(*) FROM class_memberships WHERE class_id=? AND role=?',
                    (member['class_id'], role)).fetchone()[0] for role in ('admin','teacher','student')}
        elif action.startswith(('done:', 'actual:', 'actual_custom:')) or step == 'actual':
            base.update(kind='done', title='ЕЩЁ ОДНО\nГОТОВО', subtitle='Шаг за шагом — к свободному вечеру')
            task_id = action.split(':')[1] if ':' in action else state.get('task_id')
            if member:
                task = self.db.execute('''SELECT t.*,d.actual FROM tasks t JOIN done d ON d.task_id=t.id
                    WHERE t.id=? AND t.class_id=? AND d.user_id=?''', (task_id,member['class_id'],uid)).fetchone()
                if task:
                    completed = self.db.execute('''SELECT COUNT(*) FROM done d JOIN tasks t ON t.id=d.task_id
                        WHERE d.user_id=? AND t.class_id=?''',(uid,member['class_id'])).fetchone()[0]
                    base.update(subject=task['subject'], body=task['body'], minutes=task['minutes'], actual=task['actual'],
                                completed=completed, count=base['task_count'])
        elif action in ('help', '/help'):
            base.update(kind='help', title='КАК ЭТО\nРАБОТАЕТ', subtitle='Задание → проверка → личный план')
        elif action.startswith('delete:'):
            base.update(kind='warning', title='УДАЛИТЬ\nЗАДАНИЕ?', subtitle='Подтвердите действие кнопкой ниже')
        return base

    def plan(self, uid, offset=0, page=0):
        member = self.require(uid, ('student',))
        row = self.db.execute('SELECT minutes FROM availability WHERE user_id=?', (uid,)).fetchone()
        available = json.loads(row['minutes']) if row else [120] * 7
        done_ids = {r[0] for r in self.db.execute('SELECT task_id FROM done WHERE user_id=?', (uid,))}
        tasks = self.store.tasks(member['class_id'])
        progress,spent,overrides=self.learning_totals(uid)
        plan, overdue, overflow = build_plan(tasks, done_ids, available, self.today(),progress=progress,spent=spent,overrides=overrides)
        selected = (self.today() + timedelta(days=max(offset, 0))).isoformat()
        entries = plan[selected] if offset >= 0 else [{'task': t, 'minutes': t['minutes']} for t in overdue]
        pages = max(1, (len(entries) + 2) // 3)
        page = min(page, pages - 1)
        visible = entries[page * 3:page * 3 + 3]
        lines = [f'Мой план · {member["name"]}',
                 'Просроченные задания' if offset < 0 else pretty_date(selected) + f' · {sum(e["minutes"] for e in entries)} мин', '']
        day_limit=overrides.get(selected,available[date.fromisoformat(selected).weekday()])
        day_spent=spent.get(selected,0)
        lines.append(f'На этот день: {day_limit} мин · учтено {day_spent} · осталось {max(0,day_limit-day_spent)} мин.\n')
        if not row:
            lines.append('Пока заложено 120 мин/день. Можно изменить «Моё время».\n')
        rows = []
        for entry in visible:
            task = entry['task']
            part = f' (часть из {task["minutes"]})' if entry['minutes'] < task['minutes'] else ''
            title=f'{task["subject"]} · {entry["minutes"]} мин{part}' if entry['minutes'] else task['subject']+' · проверьте готовность'
            lines.extend([title, task['body'], self.deadline(task), ''])
            if 0<entry['minutes']<self.remaining(uid,task) and offset>=0:
                token=self.part_token(uid,task,entry['minutes'])
                rows.append([button(f'Часть {entry["minutes"]} мин готова',f'part:{task["id"]}:{entry["minutes"]}:{token}')])
            rows.append([button('Всё готово: ' + task['subject'][:23], 'done:' + task['id'])])
        if not entries:
            lines.append('На этот день по добавленным заданиям работы нет.' if tasks else 'Учителя ещё не добавили задания. План пока неполный — проверьте дневник.')
        if not any(plan.values()) and not overdue and not overflow:
            if tasks:lines.append('Невыполненных добавленных заданий нет. Все добавленные задания выполнены.')
        if overflow:
            lines.append('\n⚠ Не помещается в план или до срока: ' +
                         '; '.join(f'{e["task"]["subject"]}: ещё {e["minutes"]} мин' for e in overflow[:3]))
            lines.append('Проверьте время или обсудите срок с учителем.')
        if pages > 1:
            nav = []
            if page:
                nav.append(button('‹ Задания', f'plan:{offset}:{page - 1}'))
            if page + 1 < pages:
                nav.append(button('Ещё задания ›', f'plan:{offset}:{page + 1}'))
            rows.append(nav)
        rows.append([button('Сегодня', 'plan:0:0'), button('Завтра', 'plan:1:0')])
        nav = []
        if offset > 0:
            nav.append(button('‹ День', f'plan:{offset - 1}:0'))
        if offset < 6:
            nav.append(button('Следующий день ›', f'plan:{max(offset + 1, 1)}:0'))
        if nav:
            rows.append(nav)
        if overdue:
            rows.append([button(f'Просрочено: {len(overdue)}', 'plan:-1:0')])
        last_partial=self.db.execute('''SELECT l.token FROM study_log l JOIN tasks t ON t.id=l.task_id
            LEFT JOIN done d
            ON d.user_id=l.user_id AND d.task_id=l.task_id
            WHERE l.user_id=? AND t.class_id=? AND d.task_id IS NULL ORDER BY l.id DESC LIMIT 1''',
            (uid,member['class_id'])).fetchone()
        if last_partial:
            rows.append([button('Отменить последнюю часть','undo_work:'+last_partial['token'])])
        rows.append([button('Сегодня могу…','today_time'),button('Выполнено','completed')])
        rows.append([button('Моё время', 'free'), button('Нагрузка','load')])
        lines.append('\nПлан учитывает только задания в боте. Проверьте, всё ли добавлено.')
        message = reply('\n'.join(lines), rows)
        message['_visual'] = {'kind': 'plan', 'title': 'МОЙ\nПЛАН', 'subtitle': 'Освобождаем время для себя',
                              'bars': [sum(e['minutes'] for e in values) for values in plan.values()],
                              'labels': [WEEKDAYS[(self.today() + timedelta(days=i)).weekday()] for i in range(7)],
                              'selected': offset if offset >= 0 else None, 'class_name': member['name'], 'total': sum(e['minutes'] for e in entries),
                              'count': len(entries), 'capacity': max(0,day_limit-day_spent), 'day_spent':day_spent,'day_limit':day_limit,
                              'day': pretty_date(selected), 'overdue': offset < 0,
                              'overflow_minutes': sum(e['minutes'] for e in overflow),
                              'entries': [{'subject':e['task']['subject'], 'body':e['task']['body'], 'minutes':e['minutes']} for e in visible]}
        if offset < 0:
            message['_visual'].update(title='НУЖНО\nДОДЕЛАТЬ', subtitle='Согласуйте новый срок с учителем')
        elif not tasks:
            message['_visual'].update(title='ЖДЁМ\nЗАДАНИЯ',subtitle='Учителя ещё не добавили домашку. Проверьте дневник.')
        elif not any(plan.values()) and not overdue and not overflow:
            message['_visual'].update(title='ВСЁ\nОТМЕЧЕНО',subtitle='Выполнены все добавленные в бот задания')
        return [message]

    def task_list(self, uid, page=0):
        member = self.require(uid, ('teacher', 'admin'))
        tasks = self.store.tasks(member['class_id'])
        pages = max(1, (len(tasks) + 2) // 3)
        page = min(page, pages - 1)
        lines = [f'Задания · {member["name"]} · {page + 1}/{pages}',
                 'Медиана от 3 ответов, без ID и фамилий.', '']
        rows = []
        visual_entries = []
        for task in tasks[page * 3:page * 3 + 3]:
            values = [r[0] for r in self.db.execute('SELECT actual FROM done WHERE task_id=? AND actual IS NOT NULL', (task['id'],))]
            info = (f'Медиана: {statistics.median(values):g} мин ({len(values)} ответов)' if len(values) >= 3 else
                    'Медиана: недостаточно ответов (нужно 3)')
            visual_entries.append({'subject':task['subject'], 'body':task['body'], 'minutes':task['minutes'],
                                   'due':self.deadline(task), 'median':statistics.median(values) if len(values)>=3 else None})
            if member['role'] == 'admin' or task['creator'] == uid:
                rows.append([button('Удалить: ' + task['subject'][:24], f'delete:{task["id"]}')])
            lines.append(f'{task["subject"]}: {task["body"]}\n{self.deadline(task)}, {task["minutes"]} мин\n{info}\n')
        if not tasks:
            lines.append('Пока нет заданий. Добавьте первое.')
        nav = []
        if page:
            nav.append(button('‹ Назад', f'tasks:{page - 1}'))
        if page + 1 < pages:
            nav.append(button('Дальше ›', f'tasks:{page + 1}'))
        if nav:
            rows.append(nav)
        rows.append([button('Добавить', 'add'), button('Меню', 'menu')])
        message = reply('\n'.join(lines), rows)
        message['_visual'] = {'kind': 'tasks', 'title': 'ЗАДАНИЯ\nКЛАССА', 'subtitle': 'Оценки времени и обратная связь',
                              'class_name': member['name'], 'count': len(tasks), 'total': sum(t['minutes'] for t in tasks),
                              'entries':visual_entries, 'page':page+1, 'pages':pages}
        return [message]


from workflows import WorkflowMixin
from agent import AgentMixin
from classroom import ClassroomMixin
from demo_max import DemoMixin


class Engine(DemoMixin, AgentMixin, ClassroomMixin, WorkflowMixin, LegacyEngine):
    """Текущие сценарии поверх совместимого ядра хранения и диалога."""
