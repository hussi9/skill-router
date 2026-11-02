import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import routing_descriptions as rd
import jev_choose as jev

class RoutingDescriptionsTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'name':'debug', 'source_hash':'abc', 'kind':'domain', 'type':'skill',
                      'description':'Original', 'use_when':['fails']}
        self.overlay = {'version':1,'rules':{'debug':{'expected_source_hash':'abc',
            'routing_summary':'Find root cause before fixing.', 'routing_kind':'process'}}}

    def test_preservation_idempotence(self):
        original = {'entries':[self.entry], 'total':1}
        first = rd.apply_overlay(original,self.overlay)
        self.assertEqual(original['entries'][0],self.entry)
        e=first['entries'][0]
        for key in ('description','use_when','source_hash'):
            self.assertEqual(e[key],self.entry[key])
        self.assertEqual(e['source_kind'],'domain')
        self.assertEqual(e['kind'],'process')
        self.assertEqual(first,rd.apply_overlay(first,self.overlay))
        self.assertEqual(len(first['entries']),1)

    def test_stale_rule_skipped(self):
        self.overlay['rules']['debug']['expected_source_hash']='old'
        out=rd.apply_overlay({'entries':[self.entry]},self.overlay)
        self.assertNotIn('routing_summary',out['entries'][0])
        self.assertIn('stale_source_hash',out['routing_descriptions']['warnings'][0])

    def test_removed_rule_clears_derived_metadata(self):
        first=rd.apply_overlay({'entries':[self.entry]},self.overlay)
        out=rd.apply_overlay(first,{'version':2,'rules':{}})
        self.assertEqual(out['entries'][0],self.entry)

    def test_invalid_rule_clears_derived_metadata(self):
        first=rd.apply_overlay({'entries':[self.entry]},self.overlay)
        self.overlay['rules']['debug']['routing_summary']='x'*321
        out=rd.apply_overlay(first,self.overlay)
        self.assertEqual(out['entries'][0],self.entry)
        self.assertIn('invalid_summary',out['routing_descriptions']['warnings'][0])

    def test_dedicated_summary_not_prefix_clipped(self):
        summary='A'*300+' final distinction'
        rendered=jev._summary({**self.entry,'routing_summary':summary},150)
        self.assertIn('final distinction',rendered)

    def test_chunks_keep_all_unaliased_entries(self):
        entries=[{**self.entry,'name':f'skill-{n}'} for n in range(600)]
        _,keys=jev.build_questions(entries)
        self.assertEqual(sum(len(v) for v in keys.values()),600)
