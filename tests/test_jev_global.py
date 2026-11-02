import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_global as g

class GlobalTests(unittest.TestCase):
    def entries(self,n=130):
        return [{'name':f'skill-{i}','type':'skill','kind':'domain','description':'meaningful '+ 'x'*400,'use_when':['trigger one','trigger two','trigger three']} for i in range(n)]
    def transport(self, replies):
        self.calls=[]
        def send(body,key,timeout):
            self.calls.append(body)
            return replies[len(self.calls)-1]
        return send
    def test_balanced_chunks_and_full_criteria(self):
        questions,mapping=g.build_questions(self.entries(600))
        sizes=[len(x) for x in mapping.values()]
        self.assertEqual(sum(sizes),600)
        self.assertLessEqual(max(sizes),254)
        self.assertEqual(sizes,[254,254,92])
        self.assertIn('trigger three',questions['domain_0']['criteria']['d0'])
        self.assertGreater(len(questions['domain_0']['criteria']['d0']),400)
    def test_two_stage_probabilities_and_global_threshold(self):
        transport=self.transport([{'answers':{'domain_0':{'choice':'d0','confidence':.4,'probabilities':{'d0':.4,'d1':.35,'d2':.25}}},'usage':{'input_tokens':100}}, {'answers':{'global':{'choice':'g1','confidence':.9}},'usage':{'input_tokens':50}}])
        out=g.choose('request',entries=self.entries(4),transport=transport)
        self.assertEqual(out.primary.name,'skill-1')
        self.assertEqual(out.primary.tier,'route')
        self.assertEqual(out.attempts,2)
        self.assertEqual(out.tokens,150)
        self.assertEqual(out.model,'inherit')
        self.assertEqual(len(self.calls[1]['questions']['global']['criteria']),4)
    def test_winner_fallback_and_no_process_primary(self):
        transport=self.transport([{'answers':{'domain_0':{'choice':'none','confidence':.9},'process':{'choice':'p0','confidence':.99}}}])
        entries=self.entries(2)+[{'name':'debug','kind':'process','type':'skill'}]
        out=g.choose('request',entries=entries,transport=transport)
        self.assertIsNone(out.primary.name)
        self.assertEqual(out.process.name,'debug')
        self.assertEqual(out.attempts,1)
    def test_unknown_and_nonfinite_probabilities_do_not_enter_shortlist(self):
        transport=self.transport([{'answers':{'domain_0':{'choice':'d0','confidence':.3,'probabilities':{'unknown':.99,'d0':float('nan'),'d1':.2}}}}, {'answers':{'global':{'choice':'unknown','confidence':1}}}])
        out=g.choose('request',entries=self.entries(2),transport=transport)
        self.assertIsNone(out.primary.name)
        self.assertEqual(out.failure,'malformed_global')
        self.assertEqual(len(self.calls),2)
    def test_failure_never_retries(self):
        transport=self.transport([None])
        out=g.choose('request',entries=self.entries(2),transport=transport)
        self.assertEqual(out.attempts,1)
        self.assertTrue(out.failure)
    def test_long_input_bounded_and_context_only_short(self):
        transport=self.transport([{'answers':{'domain_0':{'choice':'none','confidence':1}}}])
        out=g.choose('x'*40000,context='context',entries=self.entries(1),transport=transport)
        self.assertLessEqual(len(self.calls[0]['state']['request']),16000)
        self.assertNotIn('x'*100,out.to_dict().__str__())

    def test_current_winners_include_low_confidence_without_prefilter(self):
        entries=self.entries(300)
        out=g.current_shortlist({'domain_0':{'choice':'d0','confidence':.1},
            'domain_1':{'choice':'d254','confidence':.2}},entries)
        self.assertEqual(out,['skill-0','skill-254'])

    def test_named_winner_kept_even_if_probability_zero(self):
        selected,chunks=g.shortlist_from_answers({'domain_0':{'choice':'d0','confidence':.1,
            'probabilities':{'d0':0,'d1':.3,'d2':.3,'d3':.3,'none':.1}}},
            {'domain_0':{'d0':'zero','d1':'one','d2':'two','d3':'three'}})
        self.assertIn('zero',selected)
        self.assertEqual(len(selected),3)

    def test_partial_invalid_probabilities_fallback_selected_winner(self):
        selected,_=g.shortlist_from_answers({'domain_0':{'choice':'d0','confidence':.1,
            'probabilities':{'unknown':.8,'d1':float('inf')}}},
            {'domain_0':{'d0':'zero','d1':'one'}})
        self.assertEqual(selected,['zero'])

    def test_current_representation_uses_original_summary(self):
        transport=self.transport([{'answers':{'global':{'choice':'g0','confidence':.9}}}])
        entries=self.entries(2)
        g.choose('request',entries=entries,shortlist=['skill-0'],representation='current',transport=transport)
        self.assertEqual(self.calls[0]['questions']['global']['criteria']['g0'],g.base._summary(entries[0],150))
        self.assertEqual(len(self.calls),1)

    def test_current_top3_expands_same_answers_without_gate(self):
        entries=self.entries(4)
        raw={'domain_0':{'choice':'d0','confidence':.1,'probabilities':{'d0':.1,'d1':.5,'d2':.4,'none':0}}}
        self.assertEqual(set(g.current_shortlist(raw,entries,expand=True)),{'skill-0','skill-1','skill-2'})

    def test_unhashable_stage1_choice_fails_closed(self):
        for malicious in ([], {}):
            transport=self.transport([{'answers':{'domain_0':{'choice':malicious,'confidence':.9}}}])
            out=g.choose('request',entries=self.entries(2),transport=transport)
            self.assertEqual(out.failure,'malformed_stage1')
            self.assertIsNone(out.primary.name)

    def test_unhashable_global_choice_fails_closed(self):
        for malicious in ([], {}):
            transport=self.transport([{'answers':{'global':{'choice':malicious,'confidence':.9}}}])
            out=g.choose('request',entries=self.entries(2),shortlist=['skill-0'],transport=transport)
            self.assertEqual(out.failure,'malformed_global')
            self.assertIsNone(out.primary.name)

    def test_boolean_confidence_never_routes(self):
        transport=self.transport([{'answers':{'global':{'choice':'g0','confidence':True}}}])
        out=g.choose('request',entries=self.entries(2),shortlist=['skill-0'],transport=transport)
        self.assertEqual(out.primary.confidence,0)
        self.assertEqual(out.primary.tier,'silent')

    def test_rich_questions_keep_path_tier(self):
        questions,_=g.build_questions(self.entries(2))
        original,_=g.base.build_questions(self.entries(2))
        self.assertEqual(questions['path'],original['path'])
        self.assertEqual(questions['tier'],original['tier'])

    def test_process_overflow_fails_before_provider(self):
        entries=[{'name':f'process-{i}','kind':'process','type':'skill'} for i in range(255)]
        transport=self.transport([])
        out=g.choose('request',entries=entries,transport=transport)
        self.assertEqual(out.failure,'process_overflow')
        self.assertEqual(out.attempts,0)

    def test_invalid_deadline_fails_before_provider(self):
        for seconds in (float('nan'),float('inf'),0,-1,True):
            transport=self.transport([])
            out=g.choose('request',entries=self.entries(2),transport=transport,timeout=seconds)
            self.assertEqual(out.failure,'invalid_timeout')
            self.assertEqual(out.attempts,0)
