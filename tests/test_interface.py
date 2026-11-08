import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot import ApiError, flush_outbox
from mera import Engine, Store, parse_date


def all_buttons(messages):
    return [b for m in messages for a in m.get('attachments',[])
            if a['type']=='inline_keyboard' for row in a['payload']['buttons'] for b in row]


class FakeMAX:
    def __init__(self):
        self.uploads=[]
        self.sent=[]
        self.deleted=[]
        self.fail=False

    def upload(self,path):
        self.uploads.append(path)
        return {'photos':{'fake':{'token':'fake-token'}}}

    def send(self,uid,message):
        if self.fail:
            raise ApiError(503)
        self.sent.append((uid,message))
        return {'message':{'body':{'mid':f'm{len(self.sent)}'}}}

    def delete(self,mid):
        self.deleted.append(mid)
        return {'success':True}


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine=Engine(self.store,today=lambda:date(2026,11,5))
        self.engine.handle(1,'create')
        self.engine.handle(1,'9А')

    def start_assignment(self):
        self.engine.handle(1,'add')
        self.engine.handle(1,'subject:Алгебра')
        return self.engine.handle(1,'Страница 42, № 10')

    def test_date_is_selected_without_typing_year_or_month(self):
        picker=self.start_assignment()
        self.assertNotIn('ГГГГ',picker[0]['text'])
        self.assertNotIn('Сегодня',[b['text'] for b in all_buttons(picker)])
        self.assertIn('Завтра',[b['text'] for b in all_buttons(picker)])
        button=next(b for b in all_buttons(picker) if b['text']=='Завтра')
        minutes=self.engine.handle(1,button['payload'])
        self.assertEqual(self.store.state(1)['draft']['due'],'2026-11-05')
        self.assertEqual(self.store.state(1)['draft']['lesson_due'],'2026-11-06')
        self.assertTrue(any(b['payload']=='minutes:45' for b in all_buttons(minutes)))
        preview=self.engine.handle(1,'minutes:45')
        self.assertIn('6 ноября',preview[0]['text'])
        self.assertIn('К уроку',preview[0]['text'])

    def test_calendar_month_navigation_and_disabled_dates(self):
        self.start_assignment()
        calendar=self.engine.handle(1,'calendar:2026-11')
        dates=[b['payload'] for b in all_buttons(calendar) if b['payload'].startswith('lesson:')]
        self.assertNotIn('lesson:2026-11-05',dates)
        self.assertIn('lesson:2026-11-06',dates)
        self.assertIn('lesson:2026-11-30',dates)
        december=self.engine.handle(1,'calendar:2026-12')
        noop=self.engine.handle(1,'calendar_noop')
        self.assertEqual(noop[0]['_visual']['month'],'2026-12-01')
        self.assertEqual(december[0]['_visual']['kind'],'calendar')
        picked=self.engine.handle(1,'lesson:2026-12-03')
        self.assertEqual(self.store.state(1)['draft']['due'],'2026-12-02')
        self.assertEqual(self.store.state(1)['draft']['lesson_due'],'2026-12-03')
        for message in calendar:
            rows=message['attachments'][0]['payload']['buttons']
            self.assertLessEqual(len(rows),30)
            self.assertTrue(all(len(row)<=7 for row in rows))

    def test_stale_calendar_does_not_modify_new_state(self):
        response=self.engine.handle(1,'due:2026-11-06')
        self.assertIn('старому заданию',response[0]['text'])
        self.assertFalse(self.store.state(1))

    def test_short_dates_and_weekday_names(self):
        today=date(2026,11,5)
        for value in ('6 ноября','6.11','в пятницу'):
            self.assertEqual(parse_date(value,today),date(2026,11,6))
        self.assertEqual(parse_date('2 января',date(2026,12,31)),date(2027,1,2))

    def test_one_screen_for_plan_and_task_list(self):
        for i in range(7):
            self.start_assignment()
            self.engine.handle(1,'due:2026-11-05')
            preview=self.engine.handle(1,'minutes:15')
            self.engine.handle(1,all_buttons(preview)[0]['payload'])
        code=self.store.member(1)['student_code']
        self.engine.handle(2,'join')
        self.engine.handle(2,code)
        for command,uid in (('plan',2),('plan:0:1',2),('tasks',1),('tasks:1',1)):
            messages=self.engine.handle(uid,command)
            self.assertEqual(len(messages),1)
            self.assertLessEqual(len(messages[0]['text']),4000)
            self.assertIn('_visual',messages[0])
        self.assertTrue(any(b['payload']=='plan:0:1' for b in all_buttons(self.engine.handle(2,'plan'))))

    def test_availability_can_be_changed_with_buttons(self):
        self.engine.handle(2,'join')
        self.engine.handle(2,self.store.member(1)['student_code'])
        settings=self.engine.handle(2,'free')
        self.assertTrue(any(b['payload']=='free_day:4' for b in all_buttons(settings)))
        self.engine.handle(2,'free_day:4')
        self.engine.handle(2,'free_value:4:0')
        values=json.loads(self.store.db.execute('select minutes from availability where user_id=2').fetchone()[0])
        self.assertEqual(values,[120,120,120,120,0,120,120])

    def test_all_major_messages_include_infographics(self):
        for action in ('menu','load','manage','tasks','add','help'):
            messages=self.engine.handle(1,action)
            self.assertTrue(all(m.get('_visual',{}).get('kind') for m in messages))

    def test_delivered_screen_is_replaced_and_media_is_cached(self):
        api=FakeMAX()
        self.engine.handle(1,'menu',event_id='one')
        flush_outbox(self.store,api)
        self.assertEqual(api.deleted,[])
        self.assertEqual(api.sent[0][1]['attachments'][0]['type'],'image')
        self.assertFalse(any(k.startswith('_') for k in api.sent[0][1]))
        self.engine.handle(1,'menu',event_id='two',source_mid='m1')
        flush_outbox(self.store,api)
        self.assertEqual(api.deleted,['m1'])
        self.assertEqual(len(api.uploads),1)
        self.assertEqual(self.store.db.execute('select mid from screens where user_id=1').fetchone()[0],'m2')

    def test_old_screen_survives_failed_delivery(self):
        api=FakeMAX()
        self.engine.handle(1,'menu',event_id='one')
        flush_outbox(self.store,api)
        api.fail=True
        self.engine.handle(1,'load',event_id='two',source_mid='m1')
        with self.assertRaises(ApiError):
            flush_outbox(self.store,api)
        self.assertEqual(api.deleted,[])
        self.assertEqual(self.store.db.execute('select mid from screens where user_id=1').fetchone()[0],'m1')
        api.fail=False
        flush_outbox(self.store,api)
        self.assertEqual(api.deleted,['m1'])
        self.assertEqual(self.store.db.execute('select count(*) from outbox where sent=0').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
