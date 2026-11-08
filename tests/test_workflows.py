import sqlite3
import sys
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mera import Engine,Store


def controls(messages):
    return [b for m in messages for a in m.get('attachments',[]) if a['type']=='inline_keyboard'
            for row in a['payload']['buttons'] for b in row]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.day=date(2026,11,5)
        self.store=Store(':memory:');self.addCleanup(self.store.close)
        self.engine=Engine(self.store,today=lambda:self.day)
        self.engine.handle(1,'create');self.engine.handle(1,'9А')
        self.join(2,'teacher');self.join(3,'student')

    def join(self,uid,role):
        self.engine.handle(uid,'join');self.engine.handle(uid,self.store.member(1)[role+'_code'])

    def draft(self,uid=2,minutes=60,day=None,lesson=False,subject='Алгебра'):
        for text in ('add',subject,'Страница 42, № 8–10'):
            self.engine.handle(uid,text)
        target=day or self.day
        self.engine.handle(uid,('lesson:' if lesson else 'due:')+target.isoformat())
        return self.engine.handle(uid,str(minutes))

    def confirm(self,uid,messages):
        return self.engine.handle(uid,next(b['payload'] for b in controls(messages) if b['payload'].startswith('confirm:')))

    def publish(self,uid=2,minutes=60,day=None,lesson=False,subject='Алгебра'):
        before={t['id'] for t in self.store.tasks(1)}
        self.confirm(uid,self.draft(uid,minutes,day,lesson,subject))
        return next(t for t in self.store.tasks(1) if t['id'] not in before)

    def capacity(self,uid=3,minutes=60):
        self.engine.handle(uid,'free');self.engine.handle(uid,' '.join([str(minutes)]*7))

    def plan(self,uid=3,day=0):
        return self.engine.handle(uid,f'plan:{day}:0')[0]

    def test_completed_hour_does_not_refill_today(self):
        a=self.publish();b=self.publish(day=self.day+timedelta(days=1),subject='Физика');self.capacity()
        self.assertEqual(self.plan()['_visual']['total'],60)
        self.engine.handle(3,'done:'+a['id'])
        today=self.plan()['_visual'];tomorrow=self.plan(day=1)['_visual']
        self.assertEqual((today['day_spent'],today['capacity'],today['total']),(60,0,0))
        self.assertEqual(tomorrow['total'],60)

    def test_actual_duration_adjusts_remaining_capacity(self):
        a=self.publish();self.publish(day=self.day+timedelta(days=1),subject='Физика');self.capacity()
        self.engine.handle(3,'done:'+a['id']);self.engine.handle(3,f'actual:{a["id"]}:30')
        model=self.plan()['_visual']
        self.assertEqual((model['day_spent'],model['capacity'],model['total']),(30,30,30))

    def test_spent_time_stays_on_completion_day(self):
        a=self.publish();self.publish(day=self.day+timedelta(days=2),subject='Физика');self.capacity()
        self.engine.handle(3,'done:'+a['id']);self.day+=timedelta(days=1)
        model=self.plan()['_visual']
        self.assertEqual((model['day_spent'],model['capacity'],model['total']),(0,60,60))

    def test_partial_completion_keeps_task_open_and_is_idempotent(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        self.engine.handle(3,part);self.engine.handle(3,part)
        self.assertEqual(self.engine.remaining(3,task),60)
        self.assertEqual(self.store.db.execute('select count(*) from done').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('select count(*) from study_log').fetchone()[0],1)
        self.assertEqual(self.plan()['_visual']['total'],0)
        self.assertEqual(self.plan(day=1)['_visual']['total'],60)

    def test_undo_completion_restores_task_and_capacity_only_for_self(self):
        task=self.publish();self.capacity();self.join(4,'student')
        self.engine.handle(3,'done:'+task['id']);self.engine.handle(3,'undo:'+task['id'])
        model=self.plan()['_visual'];self.assertEqual((model['day_spent'],model['total']),(0,60))
        self.assertIn(task['subject'],self.plan(4)['text'])

    def test_undo_final_completion_preserves_previous_partial_work(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        self.engine.handle(3,part);self.engine.handle(3,'done:'+task['id'])
        self.engine.handle(3,'undo:'+task['id'])
        self.assertEqual(self.engine.remaining(3,task),60)
        self.assertEqual(self.plan()['_visual']['day_spent'],60)

    def test_partial_work_can_be_undone(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        message=self.engine.handle(3,part)
        undo=next(b['payload'] for b in controls(message) if b['payload'].startswith('undo_work:'))
        self.engine.handle(3,undo)
        self.assertEqual(self.engine.remaining(3,task),120)
        self.assertEqual(self.plan()['_visual']['day_spent'],0)

    def test_undo_partial_remains_available_after_returning_to_plan(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        self.engine.handle(3,part)
        undo=next(b['payload'] for b in controls(self.engine.handle(3,'menu')) if b['payload'].startswith('undo_work:'))
        self.engine.handle(3,undo)
        self.assertEqual(self.engine.remaining(3,task),120)

    def test_actual_total_cannot_erase_time_of_completed_parts(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        self.engine.handle(3,part);self.engine.handle(3,'done:'+task['id'])
        message=self.engine.handle(3,f'actual:{task["id"]}:30')[0]['text']
        self.assertIn('Уже учтены части',message)
        self.assertEqual(self.plan()['_visual']['day_spent'],120)

    def test_deleted_task_does_not_refund_time_already_spent(self):
        task=self.publish();self.publish(day=self.day+timedelta(days=1),subject='Физика');self.capacity()
        self.engine.handle(3,'done:'+task['id']);self.engine.handle(2,'delete_confirm:'+task['id'])
        self.assertEqual(self.plan()['_visual']['capacity'],0)
        self.assertEqual(self.store.db.execute('select task_id from study_log').fetchone()[0],None)

    def test_lesson_day_automatically_uses_previous_evening(self):
        task=self.publish(day=self.day+timedelta(days=1),lesson=True)
        self.assertEqual(task['lesson_due'],'2026-11-06');self.assertEqual(task['due'],'2026-11-05')
        model=self.engine.handle(2,'load')[0]['_visual'];self.assertEqual(model['bars'][:2],[60,0])
        self.assertIn('К уроку 6 ноября',self.plan()['text'])

    def test_lesson_today_is_not_scheduled_after_lesson(self):
        self.engine.handle(2,'add');self.engine.handle(2,'Физика');self.engine.handle(2,'№ 8')
        result=self.engine.handle(2,'lesson:'+self.day.isoformat())
        self.assertIn('Выберите дату урока',result[0]['text'])
        self.assertEqual(self.store.state(2)['step'],'due')

    def test_lesson_date_crosses_year_boundary(self):
        self.day=date(2026,12,30)
        self.engine.handle(2,'add');self.engine.handle(2,'Физика');self.engine.handle(2,'№ 8')
        self.engine.handle(2,'1 января')
        draft=self.store.state(2)['draft']
        self.assertEqual((draft['lesson_due'],draft['due']),('2027-01-01','2026-12-31'))

    def test_edit_changes_same_task_without_duplication(self):
        task=self.publish();self.engine.handle(2,f'edit_field:{task["id"]}:body')
        preview=self.engine.handle(2,'Страница 50, № 12');self.confirm(2,preview)
        self.assertEqual(len(self.store.tasks(1)),1)
        updated=self.store.tasks(1)[0]
        self.assertEqual((updated['id'],updated['body'],updated['revision']),(task['id'],'Страница 50, № 12',1))

    def test_edit_minutes_excludes_old_version_from_load(self):
        task=self.publish(minutes=150);self.engine.handle(2,f'edit_field:{task["id"]}:minutes')
        preview=self.engine.handle(2,'120')[0]
        self.assertEqual(preview['_visual']['total'],120)
        self.assertEqual(preview['_visual']['before'],0)
        self.assertNotIn('Высокая нагрузка',preview['text'])

    def test_edit_lesson_date_preserves_both_dates(self):
        task=self.publish(day=self.day+timedelta(days=1),lesson=True)
        self.engine.handle(2,f'edit_field:{task["id"]}:due')
        preview=self.engine.handle(2,'lesson:2026-11-08');self.confirm(2,preview)
        updated=self.store.tasks(1)[0]
        self.assertEqual((updated['lesson_due'],updated['due']),('2026-11-08','2026-11-07'))

    def test_other_teacher_cannot_edit_task(self):
        task=self.publish();self.join(5,'teacher')
        response=self.engine.handle(5,f'edit_field:{task["id"]}:body')[0]['text']
        self.assertIn('собственные',response)
        self.assertFalse(self.store.state(5))

    def test_concurrent_edit_is_not_silently_overwritten(self):
        task=self.publish()
        self.engine.handle(2,f'edit_field:{task["id"]}:body');teacher=self.engine.handle(2,'Текст учителя')
        self.engine.handle(1,f'edit_field:{task["id"]}:body');admin=self.engine.handle(1,'Текст администратора')
        self.confirm(1,admin);response=self.confirm(2,teacher)[0]['text']
        self.assertIn('изменил задание',response)
        self.assertEqual(self.store.tasks(1)[0]['body'],'Текст администратора')

    def test_repeat_requires_fresh_date_before_publication(self):
        task=self.publish(minutes=45)
        self.engine.handle(2,'repeat_last');state=self.store.state(2)
        self.assertEqual(state['draft']['minutes'],45)
        self.assertNotIn('due',state['draft'])
        self.assertEqual(len(self.store.tasks(1)),1)
        preview=self.engine.handle(2,'lesson:2026-11-07');self.confirm(2,preview)
        self.assertEqual(len(self.store.tasks(1)),2)

    def test_quick_entry_reuses_subject_and_minutes_but_not_body(self):
        self.publish(minutes=45,subject='История')
        self.engine.handle(2,'add_quick');state=self.store.state(2)
        self.assertEqual(state['draft'],{'subject':'История','minutes':45})
        self.engine.handle(2,'Новый параграф 15')
        preview=self.engine.handle(2,'lesson:2026-11-07')
        self.assertEqual(preview[0]['_visual']['minutes'],45)
        self.assertIn('Новый параграф 15',preview[0]['text'])

    def test_back_keeps_entered_body(self):
        self.draft(minutes=45)
        self.engine.handle(2,'back');self.engine.handle(2,'back')
        self.assertEqual(self.store.state(2)['step'],'due')
        self.assertEqual(self.store.state(2)['draft']['body'],'Страница 42, № 8–10')

    def test_suggested_date_is_clickable_and_does_not_publish(self):
        self.publish(minutes=150)
        preview=self.draft(minutes=60)
        suggestion=next(b['payload'] for b in controls(preview) if b['payload'].startswith('suggest:'))
        changed=self.engine.handle(2,suggestion)
        self.assertNotIn('Высокая нагрузка',changed[0]['text'])
        self.assertEqual(len(self.store.tasks(1)),1)

    def test_empty_class_does_not_claim_homework_is_finished(self):
        message=self.plan()
        self.assertIn('ещё не добавили',message['text'])
        self.assertNotIn('Все добавленные задания выполнены',message['text'])
        self.assertEqual(message['_visual']['title'],'ЗАДАНИЙ\nПОКА НЕТ')

    def test_today_override_does_not_change_other_days(self):
        self.publish(day=self.day+timedelta(days=2));self.capacity()
        self.engine.handle(3,'today_value:0')
        self.assertEqual(self.plan()['_visual']['capacity'],0)
        self.assertEqual(self.plan(day=1)['_visual']['capacity'],60)
        self.engine.handle(3,'today_value:default')
        self.assertEqual(self.plan()['_visual']['capacity'],60)

    def test_student_menu_opens_today_plan(self):
        self.publish()
        self.assertIn('Мой план',self.engine.handle(3,'menu')[0]['text'])

    def test_edit_draft_body_without_changing_only_estimate(self):
        preview=self.draft(minutes=60)
        change=next(b['payload'] for b in controls(preview) if b['payload'].startswith('draft_body:'))
        self.engine.handle(2,change)
        updated=self.engine.handle(2,'Только № 8')[0]
        self.assertEqual(updated['_visual']['body'],'Только № 8')
        self.assertEqual(updated['_visual']['minutes'],60)

    def test_shortened_estimate_does_not_silently_complete_partial_task(self):
        task=self.publish(minutes=120,day=self.day+timedelta(days=2));self.capacity()
        part=next(b['payload'] for b in controls([self.plan()]) if b['payload'].startswith('part:'))
        self.engine.handle(3,part)
        self.engine.handle(2,f'edit_field:{task["id"]}:minutes')
        self.confirm(2,self.engine.handle(2,'30'))
        self.assertIn('проверьте готовность',self.plan()['text'])
        self.assertEqual(self.store.db.execute('select count(*) from done').fetchone()[0],0)

    def test_teacher_sees_existing_completions_before_edit_is_saved(self):
        task=self.publish();self.engine.handle(3,'done:'+task['id'])
        self.engine.handle(2,f'edit_field:{task["id"]}:body')
        preview=self.engine.handle(2,'Исправление опечатки')
        self.assertIn('Их отметки сохранятся',preview[0]['text'])
        self.confirm(2,preview)
        self.assertEqual(self.plan()['_visual']['day_spent'],60)
        self.assertEqual(self.store.db.execute('select count(*) from done').fetchone()[0],1)


class MigrationTests(unittest.TestCase):
    def test_old_database_preserves_evening_dates_and_old_completions(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'old.sqlite3'
            db=sqlite3.connect(path)
            db.executescript('''CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT,daily_limit INTEGER,student_code TEXT,teacher_code TEXT);
                CREATE TABLE members(user_id INTEGER PRIMARY KEY,class_id INTEGER,role TEXT);
                CREATE TABLE tasks(id TEXT PRIMARY KEY,class_id INTEGER,creator INTEGER,subject TEXT,body TEXT,due TEXT,minutes INTEGER);
                CREATE TABLE done(user_id INTEGER,task_id TEXT,actual INTEGER,PRIMARY KEY(user_id,task_id));
                INSERT INTO classes VALUES(1,'9A',180,'student','teacher');
                INSERT INTO members VALUES(1,1,'admin');INSERT INTO members VALUES(2,1,'student');
                INSERT INTO tasks VALUES('old',1,1,'Math','page 42','2026-11-06',45);
                INSERT INTO done VALUES(2,'old',30);''')
            db.close();store=Store(path)
            try:
                task=store.tasks(1)[0]
                self.assertEqual(task['due'],'2026-11-06');self.assertIsNone(task['lesson_due'])
                engine=Engine(store,today=lambda:date(2026,11,5))
                self.assertEqual(engine.plan(2)[0]['_visual']['day_spent'],0)
                engine.handle(2,'undo:old')
                self.assertEqual(store.db.execute('select count(*) from done').fetchone()[0],0)
            finally:store.close()


if __name__=='__main__':unittest.main()
