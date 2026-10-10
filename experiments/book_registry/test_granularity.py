"""Structural triage must neither discard true content nor judge prose by keywords."""
from copy import deepcopy
import unittest
from semantic_checks import granularity_candidates,inspect,require_valid
from test_registry import batch,fact,span


def dialogue(content='你叫什么名字？'):
    value=batch('SU-000001')
    value['entries'].append({'ref':'u','existing_id':None,'kind':'utterance','name':'一次问话',
                             'evidence':[span('SU-000001','甲')]})
    value['facts']=[fact('speaker','u','a',span('SU-000001','甲'),'speaker'),
                    fact('listener','u','b',span('SU-000001','乙'),'addressee')]
    speech=fact('content','u',None,span('SU-000001','甲'),'content','attribute')
    speech.update(value=content,attribution='character',speaker='a')
    value['facts'].append(speech)
    return value


class GranularityTests(unittest.TestCase):
    def test_plain_speech_roles_raise_a_review_candidate_without_mutating_payload(self):
        value=dialogue();original=deepcopy(value)
        self.assertEqual([c['ref'] for c in granularity_candidates(value)],['u'])
        report=require_valid(value)
        self.assertIn('independent_information_gain_unchecked',[i['code'] for i in report['issues']])
        self.assertEqual(report['errors'],0)
        self.assertEqual(value,original)

    def test_explicit_dependency_is_not_a_role_only_shell(self):
        value=dialogue('我答应归还玉简。')
        value['facts'].append(fact('promise','a','u',span('SU-000001','甲'),'promises'))
        self.assertEqual(granularity_candidates(value),[])

    def test_consequential_content_is_not_automatically_deleted_by_structural_heuristic(self):
        value=dialogue('如果你放下玉简，我保证让你离开。')
        self.assertEqual(len(granularity_candidates(value)),1)
        self.assertEqual(require_valid(value)['errors'],0)
        self.assertEqual(len(value['facts']),3)

    def test_entity_attributes_and_other_event_participants_are_not_flagged(self):
        value=batch('SU-000001')
        value['entries'][1]['kind']='event'
        value['facts'][0]['predicate']='patient'
        self.assertEqual(granularity_candidates(value),[])


if __name__=='__main__':unittest.main()
