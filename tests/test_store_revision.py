import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app
from engine import EngineError, MarketingEngine, _validated_review_issues, verify_edited_text, check_store_history
from personalization import build_personalization, novelty_report, quick_intake


class QuickIntakeTests(unittest.TestCase):
    def intake(self, brief, **extra):
        return quick_intake({'brief':brief,**extra},app.PRODUCTS,app.SCENES,app.PLATFORMS)

    def test_exact_variant_and_unregistered_new_suffix(self):
        self.assertEqual(self.intake('MateBook Pro S 写朋友圈')['payload']['product'],'HUAWEI MateBook Pro S')
        for brief in ('新品 MateBook Pro 2026 写朋友圈','新品 MateBook Pro X 写朋友圈','MateBook 14写小红书','MateBook Pro和MateBook Pro S比较'):
            result=self.intake(brief)
            self.assertEqual(result['status'],'needs_clarification',brief)
            self.assertEqual(result['payload']['product'],'auto',brief)
            self.assertLessEqual(len(result['questions']),2)

    def test_fixed_full_model_and_one_sentence_need_no_extra_questions(self):
        result=self.intake('写一条面向新生的朋友圈',product='matebook14-ultra')
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['payload']['audience'],'大学新生')
        self.assertEqual(result['payload']['platform'],'门店朋友圈')
        self.assertEqual(result['payload']['scene'],'开学季')

    def test_deterministic_plan_and_substantive_store_changes(self):
        shared={'product':'MateBook 14 酷睿 Ultra','platform':'门店朋友圈','scene':'门店到访','brief':'介绍这款电脑'}
        campus=build_personalization({**shared,'store_profile':{'store_name':'甲店','store_type':'校园店','customer_question':'论文屏幕怎么选？'}})
        business=build_personalization({**shared,'store_profile':{'store_name':'乙店','store_type':'商务区店','customer_question':'会议接口怎么选？'}})
        self.assertNotEqual(campus['creative_brief']['fact_priority'],business['creative_brief']['fact_priority'])
        self.assertNotEqual(campus['creative_brief']['central_question'],business['creative_brief']['central_question'])
        self.assertEqual(campus,build_personalization({**shared,'store_profile':{'store_name':'甲店','store_type':'校园店','customer_question':'论文屏幕怎么选？'}}))
        self.assertNotEqual(campus['variant_id'],business['variant_id'])


class VerificationTests(unittest.TestCase):
    def test_edit_review_preserves_only_returned_known_fact_ids(self):
        facts=[{'id':'F1','text':'采用OLED屏幕。','quote':'采用OLED屏幕。'},
               {'id':'F2','text':'系统版本可选择。','quote':'系统版本可选择。'}]
        review={'reviewed_paths':['edited.0','edited.1'],'checks':[
            {'path':'edited.0','status':'pass','claim':'屏幕介绍','evidence':'标题','fact_ids':[]},
            {'path':'edited.1','status':'pass','claim':'采用OLED屏幕。','evidence':'原文','fact_ids':['F1','F999']}],
            'issues':[]}
        with patch.dict('os.environ',{'MODEL_API_URL':'https://example.invalid/chat/completions',
                                    'MODEL_API_KEY':'test-value','MODEL_NAME':'test-model'},clear=True), \
             patch.object(MarketingEngine,'_chat',return_value=review):
            result=verify_edited_text('屏幕介绍\n采用OLED屏幕。',facts)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['checks'][1]['fact_ids'],['F1'])

    def test_customer_question_does_not_prove_store_history(self):
        profile={'customer_question':'写论文屏幕怎么选？','experience_notes':''}
        for text in ('我们常被问到：写论文屏幕怎么选？','家里换电脑，很多顾客先问旧软件还能不能用。','今天有顾客过来问软件兼容。'):
            self.assertTrue(check_store_history(text,profile),text)
        self.assertFalse(check_store_history('今天聊聊写论文时屏幕怎么选。',profile))
        self.assertFalse(check_store_history('不写今天有顾客来问。',profile))
    def test_coverage_claim_without_actual_judgments_cannot_pass(self):
        paths={'title':'标题','sections.0':'正文'}
        review={'reviewed_paths':list(paths),'checks':[{'path':'title','status':'pass','claim':'标题','evidence':'原文'}],'issues':[]}
        with self.assertRaises(EngineError):_validated_review_issues(review,paths)

    def test_unsupported_check_is_actionable_without_issues_entry(self):
        paths={'title':'性能翻倍'}
        review={'reviewed_paths':['title'],'checks':[{'path':'title','status':'unsupported','claim':'性能翻倍','evidence':'未有比较依据'}],'issues':[]}
        self.assertEqual(len(_validated_review_issues(review,paths)),1)

    def test_no_facts_and_disconnected_verifier_cannot_return_verified(self):
        with patch.object(MarketingEngine,'_chat') as chat:
            result=verify_edited_text('欢迎看看这台电脑。',[])
            self.assertEqual(result['status'],'unverified')
            self.assertFalse(result['fact_check_complete'])
            chat.assert_not_called()
        with patch.dict('os.environ',{},clear=True):
            result=verify_edited_text('欢迎看看这台电脑。',[{'id':'F1','text':'屏幕尺寸14.2英寸','quote':'屏幕尺寸14.2英寸'}])
            self.assertEqual(result['status'],'unverified')

    def test_store_notes_never_become_fact_extraction_documents(self):
        model=MarketingEngine()
        model.url,model.key,model.model='https://example.invalid/chat/completions','test-value','test-model'
        captured=[]
        def stop(instruction,data,max_tokens=4500):
            captured.append(data)
            raise EngineError('stopped before network')
        model._chat=stop
        payload={'product':'MateBook 14 酷睿 Ultra','audience':'顾客','scene':'门店到访','platform':'门店海报','brief':'日常介绍',
                 'store_profile':{'store_name':'测试店','experience_notes':'续航必达99小时，所有软件兼容'}}
        materials={'product_name':payload['product'],'documents':[{'title':'产品原文','text':'屏幕尺寸14.2英寸，屏幕比例3:2。','source':{}}],
                   'images':[{'id':'I1','url':'/mock.png','product_name':payload['product']}]}
        with patch('public_evidence.build_public_evidence',return_value=materials):model.generate(payload,materials)
        self.assertEqual(len(captured),1)
        self.assertNotIn('99小时',str(captured[0]))
        self.assertNotIn('所有软件兼容',str(captured[0]))

    def test_incorrect_fact_relation_removed_even_with_real_quote(self):
        model=MarketingEngine()
        model.url,model.key,model.model='https://example.invalid/chat/completions','test-value','test-model'
        payload={'product':'MateBook 14 酷睿 Ultra','audience':'顾客','scene':'门店到访','platform':'门店海报','brief':'日常介绍'}
        materials={'product_name':payload['product'],'documents':[{'title':'产品原文','text':'屏幕尺寸14.2英寸，屏幕比例3:2。整机重量约1.31 kg。','source':{}}],
                   'images':[{'id':'I1','url':'/mock.png','product_name':payload['product'],'description':'电脑产品图'}]}
        def fake_chat(instruction,data,max_tokens=4500):
            if 'documents' in data:
                return {'facts':[{'text':'机身厚度14.2英寸','doc_id':'D1','quote':'屏幕尺寸14.2英寸，屏幕比例3:2。','category':'尺寸'},
                                 {'text':'整机重量约1.31 kg。','doc_id':'D1','quote':'整机重量约1.31 kg。','category':'重量'}],'conflicts':[]}
            if 'writing_context' in data:
                return {'article':{'title':'看看这台电脑','lead':'','sections':[{'heading':'尺寸','text':'机身厚度14.2英寸。','fact_ids':['F1']},
                                      {'heading':'携带','text':'整机重量约1.31 kg。','fact_ids':['F2']}],'cta':'欢迎到店看看。','tags':[]},
                        'images':[{'id':'I1','position':'正文之后','caption':'电脑产品图'}]}
            paths=data['content_paths']
            return {'reviewed_paths':list(paths),'checks':[{'path':path,'status':'unsupported' if path=='facts.F1' else 'pass',
                                                          'claim':text,'fact_ids':[],'evidence':'尺寸标签误读' if path=='facts.F1' else '测试引用'} for path,text in paths.items()],'issues':[]}
        model._chat=fake_chat
        with patch('public_evidence.build_public_evidence',return_value=materials):result=model.generate(payload,materials)
        self.assertEqual(result['status'],'success',result.get('message'))
        self.assertEqual([f['id'] for f in result['facts']],['F2'])
        self.assertNotIn('机身厚度',str(result['article']))


class PersistenceAndExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=Path(self.temp.name)
        self.patch=patch.object(app,'RUNTIME',self.runtime)
        self.patch.start()
        self.run_id='a'*32
        self.result={'run_id':self.run_id,'status':'success','input':{'product':'MateBook 14 酷睿 Ultra','platform':'门店朋友圈'},
                     'article':{'title':'测试<&>标题','lead':'','sections':[{'heading':'','text':'精确编辑后的正文<&>。','fact_ids':[]}],'cta':'','tags':[]},
                     'facts':[],'images':[],'markdown':'精确编辑后的正文','generated_at':'2026-10-19T00:00:00+00:00'}

    def tearDown(self):
        app._runs.pop(self.run_id,None)
        self.patch.stop()
        self.temp.cleanup()

    def test_restart_recovers_original_body_and_rejects_path_traversal(self):
        app.save_run(self.result)
        app._runs.pop(self.run_id,None)
        self.assertEqual(app.restore_run(self.run_id)['article'],self.result['article'])
        self.assertIsNone(app.restore_run('../outside'))
        self.assertIsNone(app.restore_run('not-found'))

    def test_portable_export_keeps_exact_body_escaped(self):
        document,assets=app.render_html_export(self.result)
        self.assertIn('精确编辑后的正文&lt;&amp;&gt;。',document)
        self.assertIn('测试&lt;&amp;&gt;标题',document)
        self.assertEqual(assets,[])

    def test_failed_edit_keeps_original_and_returns_no_new_run(self):
        app._runs[self.run_id]=copy.deepcopy(self.result)
        blocked={'status':'blocked','checks':[],'message':'没有依据','fact_check_complete':True}
        with patch('engine.verify_edited_text',return_value=blocked):
            result=app.revise(app.ReviseRequest(run_id=self.run_id,title='新标题',text='保证所有软件兼容'))
        self.assertEqual(result['status'],'blocked')
        self.assertNotIn('run_id',result)
        self.assertEqual(app._runs[self.run_id]['article'],self.result['article'])

    def test_edit_binds_body_facts_without_title_or_unused_material(self):
        original=copy.deepcopy(self.result)
        original['facts']=[{'id':fid} for fid in ('F1','F2','F3','F4')]
        app._runs[self.run_id]=original
        passed={'status':'ok','fact_check_complete':True,'checks':[
            {'status':'pass','path':'edited.0','fact_ids':['F1']},
            {'status':'pass','path':'edited.1','fact_ids':['F2','F3','F999']}]}
        with patch('engine.verify_edited_text',return_value=passed),patch('engine._render_markdown',return_value='编辑稿'):
            result=app.revise(app.ReviseRequest(run_id=self.run_id,title='产品标题',text='只介绍屏幕与系统。'))
        try:
            self.assertEqual(result['article']['sections'][0]['fact_ids'],['F2','F3'])
            self.assertEqual(result['edit_verification']['title_fact_ids'],['F1'])
            self.assertEqual([fact['id'] for fact in result['facts']],['F1','F2','F3','F4'])
            self.assertEqual(original['article'],self.result['article'])
        finally:app._runs.pop(result.get('run_id'),None)

    def test_legacy_edit_exports_review_sources_without_invented_inline_citations(self):
        legacy=copy.deepcopy(self.result)
        legacy['mode']='verified_edit'
        legacy['strategy']={'audience':'顾客','scene':'门店到访','goal':'介绍','platform':'门店朋友圈','angle':'屏幕'}
        legacy['facts']=[{'id':fid,'text':label,'quote':label,'source_title':'产品原文',
                          'source_url':'https://consumer.huawei.com/'} for fid,label in [('F1','屏幕原文'),('F2','未被正文采用的重量资料')]]
        legacy['article']['sections'][0]['fact_ids']=['F1','F2']
        legacy['checks']=[{'status':'pass','path':'edited.1','claim':'已核验正文','evidence':'原文'}]
        app.save_run(legacy)
        persisted=(self.runtime/'runs'/f'{self.run_id}.json').read_bytes()
        restored=app.restore_run(self.run_id)
        self.assertEqual(restored['article']['sections'][0]['fact_ids'],[])
        markdown=app.export_run(self.run_id,format='md').body.decode()
        self.assertIn('核验使用的资料',markdown)
        self.assertIn('未被正文采用的重量资料',markdown)
        self.assertNotIn('事实引用：',markdown)
        self.assertEqual(persisted,(self.runtime/'runs'/f'{self.run_id}.json').read_bytes())

    def test_successful_edit_keeps_original_timing_and_measures_this_check(self):
        original=copy.deepcopy(self.result)
        original['elapsed_ms']=87580
        app._runs[self.run_id]=original
        passed={'status':'ok','checks':[],'fact_check_complete':True}
        with patch('engine.verify_edited_text',return_value=passed), \
             patch('engine._render_markdown',return_value='修改后的正文'), \
             patch.object(app.time,'monotonic',side_effect=[100.0,102.5]):
            result=app.revise(app.ReviseRequest(run_id=self.run_id,title='新标题',text='修改后的正文'))
        try:
            self.assertEqual(result['elapsed_ms'],2500)
            self.assertEqual(result['original_elapsed_ms'],87580)
            self.assertEqual(result['original_generated_at'],original['generated_at'])
            self.assertEqual(original['elapsed_ms'],87580)
        finally:
            app._runs.pop(result.get('run_id'),None)

    def test_zip_markdown_and_html_use_the_same_packaged_image(self):
        selected=self.runtime/'ima-selected'
        selected.mkdir()
        image_path=selected/('b'*64+'.png')
        image_path.write_bytes(b'packaged-image-bytes')
        image_id='c'*18
        image_url=f'/api/media/{self.run_id}/{image_id}'
        app._media[(self.run_id,image_id)]=image_path
        self.result['images']=[{'url':image_url,'caption':'同款产品图'}]
        self.result['markdown']=f'精确编辑后的正文\n\n![同款产品图]({image_url})\n'
        app._runs[self.run_id]=self.result
        try:
            response=app.export_run(self.run_id,format='zip')
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                self.assertIn('![同款产品图](images/image-1.png)',archive.read('稿件.md').decode())
                self.assertIn('src="images/image-1.png"',archive.read('图文.html').decode())
                self.assertEqual(archive.read('images/image-1.png'),image_path.read_bytes())
        finally:
            app._media.pop((self.run_id,image_id),None)


class EcosystemTests(unittest.TestCase):
    def test_exact_model_and_source_provider_remain_bound(self):
        from ecosystem import select_documents
        documents,manifest=select_documents('HUAWEI MateBook Pro',{'scene':'生态体验','brief':'手机记下备忘，接到电脑继续整理'})
        self.assertEqual(len(documents),2)
        self.assertEqual(manifest['provider'],'official_huawei')
        self.assertTrue(all(item['source']['provider']=='official_huawei' for item in documents))
        self.assertTrue(any('WLAN' in item['text'] and '蓝牙' in item['text'] for item in documents))
        windows,_=select_documents('MateBook 14 酷睿 Ultra',{'scene':'生态体验','brief':'跨设备接续'})
        self.assertEqual(windows,[])

    def test_feature_and_exclusion_documents_are_selected_together(self):
        from ecosystem import select_documents
        documents,manifest=select_documents('HUAWEI MateBook 14（鸿蒙版）',{'scene':'生态体验','brief':'键鼠共享'})
        self.assertEqual(set(manifest['source_ids']),{'HW-ECO-KEYBOARD-OPERATION','HW-ECO-KEYBOARD-DEVICES'})
        corpus=''.join(item['text'] for item in documents)
        self.assertIn('5.0.0.125',corpus)
        self.assertIn('90',corpus)
        self.assertIn('70X',corpus)

    def test_missing_requested_feature_is_identified_without_dropping_valid_sources(self):
        from ecosystem import select_documents
        documents,manifest=select_documents('HUAWEI MateBook Fold 非凡大师',{'scene':'生态体验','brief':'平板副屏'})
        # The support explanation itself establishes Fold's feature and version
        # condition even though the generic operation page does not list Fold.
        self.assertEqual(len(documents),1)
        self.assertIn('7.0',documents[0]['text'])
        self.assertEqual(manifest['missing_topics'],[])
        documents,manifest=select_documents('HUAWEI MateBook Fold 非凡大师',{'scene':'生态体验','brief':'接续和键鼠共享'})
        self.assertEqual(len(documents),2)
        self.assertEqual(manifest['available_topics'],['接续'])
        self.assertEqual(manifest['missing_topics'],['键鼠共享'])
        documents,manifest=select_documents('HUAWEI MateBook Fold 非凡大师',{'scene':'生态体验','brief':'介绍生态使用场景'})
        self.assertEqual(manifest['missing_topics'],[])
        self.assertEqual(manifest['topics'],['接续','平板副屏'])


if __name__=='__main__':unittest.main()
