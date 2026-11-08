import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mera import Store,Engine
from cards import render_card,model_hash
from bot import prepare_message


class CardDataTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine=Engine(self.store,today=lambda:date(2026,11,5))
        self.engine.handle(1,'create')
        self.engine.handle(1,'9А')

    def draft(self,minutes=45):
        for action in ('add','subject:Алгебра','Страница 42, № 8–10','due:2026-11-06'):
            self.engine.handle(1,action)
        return self.engine.handle(1,f'minutes:{minutes}')[0]

    def publish(self,minutes=45):
        message=self.draft(minutes)
        self.engine.handle(1,message['attachments'][0]['payload']['buttons'][0][0]['payload'])
        return self.store.tasks(1)[-1]

    def student(self):
        self.engine.handle(2,'join')
        self.engine.handle(2,self.store.member(1)['student_code'])

    def changed_image(self,model,key,value):
        with tempfile.TemporaryDirectory() as folder:
            a=render_card(model,folder).read_bytes()
            modified={**model,key:value}
            b=render_card(modified,folder).read_bytes()
            self.assertNotEqual(a,b,f'{key} changed in model but not in PNG pixels')
            self.assertNotEqual(model_hash(model),model_hash(modified))

    def test_entered_subject_body_and_due_are_in_wizard_and_calendar(self):
        self.engine.handle(1,'add')
        subject=self.engine.handle(1,'subject:Физика')[0]['_visual']
        self.assertEqual(subject['subject'],'Физика')
        calendar=self.engine.handle(1,'Страница 37, № 15')[0]['_visual']
        self.assertEqual(calendar['body'],'Страница 37, № 15')
        wizard=self.engine.handle(1,'due:2026-11-06')[0]['_visual']
        self.assertEqual(wizard['due'],'6 ноября, Пт')
        self.changed_image(wizard,'body','Страница 99, № 18')
        self.changed_image(wizard,'subject','Геометрия')
        self.changed_image(wizard,'due','7 ноября, Сб')
        self.changed_image(calendar,'body','Страница 88, № 16')

    def test_preview_shows_before_added_and_total(self):
        self.publish(120)
        model=self.draft(75)['_visual']
        self.assertEqual((model['before'],model['minutes'],model['total'],model['limit']),(120,75,195,180))
        self.assertEqual(model['body'],'Страница 42, № 8–10')
        self.changed_image(model,'minutes',90)
        self.changed_image(model,'body','Страница 80, № 19')
        self.changed_image(model,'due','7 ноября, Сб')

    def test_changing_minutes_updates_the_entire_infographic_model(self):
        first=self.draft(45)
        control=next(b for row in first['attachments'][0]['payload']['buttons'] for b in row
                     if b['payload'].startswith('editminutes:'))
        self.engine.handle(1,control['payload'])
        second=self.engine.handle(1,'minutes:90')[0]
        self.assertEqual(second['_visual']['minutes'],90)
        self.assertEqual(second['_visual']['total'],90)
        self.assertNotEqual(first['_visual'],second['_visual'])

    def test_settings_chart_contains_actual_weekly_minutes(self):
        self.student()
        first=self.engine.handle(2,'free')[0]['_visual']
        self.assertEqual(first['bars'],[120]*7)
        second=self.engine.handle(2,'free_value:4:45')[0]['_visual']
        self.assertEqual(second['bars'][4],45)
        self.assertEqual(second['total'],765)
        self.changed_image(first,'bars',second['bars'])

    def test_task_list_and_plan_include_minutes_from_database(self):
        self.publish(45)
        task_model=self.engine.handle(1,'tasks')[0]['_visual']
        self.assertEqual(task_model['entries'][0]['minutes'],45)
        self.assertEqual(task_model['entries'][0]['body'],'Страница 42, № 8–10')
        changed=[{**task_model['entries'][0],'minutes':75}]
        self.changed_image(task_model,'entries',changed)
        self.student()
        plan_model=self.engine.handle(2,'plan')[0]['_visual']
        self.assertEqual(plan_model['total'],45)
        self.assertEqual(plan_model['entries'][0]['minutes'],45)
        self.changed_image(plan_model,'entries',[{**plan_model['entries'][0],'minutes':25}])

    def test_done_card_has_estimate_and_actual_duration(self):
        task=self.publish(45)
        self.student()
        first=self.engine.handle(2,'done:'+task['id'])[0]['_visual']
        self.assertEqual(first['minutes'],45)
        self.assertIsNone(first['actual'])
        second=self.engine.handle(2,f'actual:{task["id"]}:30')[0]['_visual']
        self.assertEqual((second['minutes'],second['actual'],second['completed'],second['count']),(45,30,1,1))
        self.changed_image(first,'actual',30)

    def test_class_management_uses_real_counts_and_limit(self):
        self.student()
        model=self.engine.handle(1,'manage')[0]['_visual']
        self.assertEqual(model['role_counts'],{'admin':1,'teacher':0,'student':1})
        self.engine.handle(1,'limit')
        changed=self.engine.handle(1,'240')[0]['_visual']
        self.assertEqual(changed['limit'],240)
        self.assertEqual(changed['kind'],'manage')
        self.changed_image(model,'limit',240)

    def test_changed_values_upload_a_new_image_not_stale_media(self):
        class API:
            uploads=[]
            def upload(self,path):
                self.uploads.append(Path(path))
                return {'token':str(len(self.uploads))}
        api=API()
        first=self.draft(45)
        prepare_message(self.store,api,first)
        prepare_message(self.store,api,first)
        self.assertEqual(len(api.uploads),1)
        changed={**first,'_visual':{**first['_visual'],'minutes':90,'total':90}}
        prepare_message(self.store,api,changed)
        self.assertEqual(len(api.uploads),2)
        self.assertNotEqual(api.uploads[0].read_bytes(),api.uploads[1].read_bytes())


if __name__=='__main__':
    unittest.main()
