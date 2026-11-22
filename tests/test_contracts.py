"""Contract-only checks. Does not claim to test a running service or model."""
import json
import unittest
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / 'contracts'
TOOLS = {'parse_report', 'search_evidence', 'get_rulebook',
         'extract_metrics', 'validate_metrics', 'compare_years',
         'save_check_proposals', 'calculate_score', 'get_task_results', 'export_analysis'}


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class ContractsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = read(CONTRACTS / 'schema.json')
        cls.manifest = read(CONTRACTS / 'tools.json')
        cls.common = cls.schema['$defs']

    def validator(self, tool, kind):
        return Draft202012Validator(
            {'$schema': self.schema['$schema'], '$defs': self.common,
             '$ref': f'#/$defs/{tool}_{kind}'}, format_checker=FormatChecker())

    def candidate(self):
        record = self.example('extract_metrics', 'success')['data']['records'][0]
        return {key: value for key, value in record.items()
                if key in self.common['MetricCandidate']['properties']}

    def validate_candidate(self, value):
        validator = Draft202012Validator(
            {'$schema': self.schema['$schema'], '$defs': self.common,
             '$ref': '#/$defs/MetricCandidate'}, format_checker=FormatChecker())
        validator.validate(value)

    def assert_candidate_invalid(self, value):
        from jsonschema import ValidationError
        with self.assertRaises(ValidationError):
            self.validate_candidate(value)

    def example(self, tool, kind):
        return read(CONTRACTS / 'examples' / f'{tool}.{kind}.json')

    def assert_invalid(self, tool, kind, value):
        self.assertTrue(list(self.validator(tool, kind).iter_errors(value)))

    def test_all_ten_tools_and_thirty_examples_validate(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual({t['name'] for t in self.manifest['tools']}, TOOLS)
        self.assertEqual(len(self.manifest['tools']), 10)
        self.assertEqual(self.manifest['contract_version'], '0.2.0')
        self.assertTrue(self.schema['$id'].endswith(':0.2.0'))
        for tool in TOOLS:
            for kind in ('request', 'success', 'error'):
                with self.subTest(tool=tool, kind=kind):
                    schema_kind = 'input' if kind == 'request' else 'output'
                    self.validator(tool, schema_kind).validate(self.example(tool, kind))

    def test_unknown_input_fields_and_page_zero_rejected(self):
        value = self.example('search_evidence', 'request')
        value['file_path'] = '/etc/passwd'
        self.assert_invalid('search_evidence', 'input', value)
        value = self.example('search_evidence', 'success')
        value['data']['evidence'][0]['pdf_page'] = 0
        self.assert_invalid('search_evidence', 'output', value)

    def test_decimal_must_be_string_and_missing_must_be_null(self):
        value = self.candidate()
        value['value'] = 100
        self.assert_candidate_invalid(value)
        value = self.candidate()
        value.update(status='missing_marker', raw_value='/', value='0')
        self.assert_candidate_invalid(value)
        value['value'] = None
        self.validate_candidate(value)

    def test_model_cannot_submit_candidates_or_manual_confirmation(self):
        self.assertNotIn('save_metric_candidates_input', self.common)
        self.assertNotIn('save_metric_candidates', {t['name'] for t in self.manifest['tools']})
        for field, content in [('candidates', [self.candidate()]),
                               ('review_state', 'confirmed'), ('model_id', 'arbitrary-model'),
                               ('record_ids', ['foreign_record'])]:
            value = self.example('extract_metrics', 'request')
            value[field] = content
            self.assert_invalid('extract_metrics', 'input', value)
        value = self.candidate()
        value['origin'] = 'manual'
        self.assert_candidate_invalid(value)
        value = self.example('save_check_proposals', 'request')
        value['checks'][0]['status'] = 'not_applicable'
        self.assert_invalid('save_check_proposals', 'input', value)

    def test_mutation_requires_revision_and_extraction_scope_is_bounded(self):
        for tool in ('parse_report', 'extract_metrics', 'save_check_proposals', 'compare_years'):
            value = self.example(tool, 'request')
            value.pop('expected_revision')
            self.assert_invalid(tool, 'input', value)
        value = self.example('extract_metrics', 'request')
        value['metric_ids'] = ['E04', 'E04']
        self.assert_invalid('extract_metrics', 'input', value)
        value['metric_ids'] = ['E99']
        self.assert_invalid('extract_metrics', 'input', value)
        value = self.example('extract_metrics', 'request')
        value['years'] = []
        self.assert_invalid('extract_metrics', 'input', value)

    def test_error_and_success_envelopes_cannot_mix(self):
        value = self.example('search_evidence', 'error')
        value['data'] = {'evidence': []}
        self.assert_invalid('search_evidence', 'output', value)
        value = self.example('search_evidence', 'success')
        value['error'] = {'code': 'PARSE_FAILED', 'message': 'error', 'retryable': False}
        self.assert_invalid('search_evidence', 'output', value)

    def test_no_ordinary_percent_for_noncomparable(self):
        value = self.example('compare_years', 'success')
        value['data']['status'] = 'needs_review'
        self.assert_invalid('compare_years', 'output', value)
        value['data'].update(delta=None, percent_change=None,
                             reason_codes=['BOUNDARY_UNCONFIRMED'])
        self.validator('compare_years', 'output').validate(value)

    def test_partial_score_cannot_be_final(self):
        value = self.example('calculate_score', 'success')
        self.assertIsNone(value['data']['final_score'])
        value['data']['final_score'] = '75'
        self.assert_invalid('calculate_score', 'output', value)

    def test_mock_examples_are_consistent_not_production_claims(self):
        rulebook = self.example('get_rulebook', 'success')['data']
        self.assertEqual(len({c['check_id'] for c in rulebook['checks']}), 20)
        self.assertEqual(sum(Decimal(c['weight']) for c in rulebook['checks']), Decimal('1'))
        score = self.example('calculate_score', 'success')['data']
        self.assertEqual(Decimal(score['coverage']), Decimal('0.125'))
        self.assertEqual(Decimal(score['reference_score']), Decimal('75'))
        self.assertEqual(Decimal(score['lower_bound']), Decimal('9.375'))
        self.assertEqual(Decimal(score['upper_bound']), Decimal('96.875'))
        for name in TOOLS:
            request = self.example(name, 'request')
            self.assertEqual(request['task_id'], 'mock_task_001')
            self.assertEqual(self.example(name, 'success')['task_id'], request['task_id'])

    def test_unauthorized_error_does_not_leak_revision(self):
        value = self.example('get_task_results', 'error')
        self.assertIsNone(value['task_id'])
        self.assertIsNone(value['revision'])
        value['revision'] = 7
        self.assert_invalid('get_task_results', 'output', value)

    def test_unknown_has_no_value_and_explains_why(self):
        value = self.candidate()
        value.update(status='unknown', value=None, raw_value=None,
                     evidence_ids=[], extraction_note='解析不足，未定位到指标。')
        self.validate_candidate(value)
        value['extraction_note'] = ''
        self.assert_candidate_invalid(value)

    def test_extraction_carries_backend_run_and_usage(self):
        result = self.example('extract_metrics', 'success')['data']
        self.assertEqual(result['run']['owner'], 'backend')
        self.assertEqual(result['run']['metric_ids'], ['E04'])
        self.assertEqual(result['run']['years'], [2024])
        self.assertEqual(result['records'][0]['metric_year'], 2024)
        self.assertEqual(result['run']['record_ids'],
                         [record['record_id'] for record in result['records']])
        value = self.example('extract_metrics', 'success')
        value['data']['run']['owner'] = 'harness'
        self.assert_invalid('extract_metrics', 'output', value)

    def test_completed_extraction_cannot_hide_unknown_records(self):
        value = self.example('extract_metrics', 'success')
        record = value['data']['records'][0]
        record.update(status='unknown', value=None, raw_value=None, evidence_ids=[],
                      extraction_note='未定位到原文。')
        self.assert_invalid('extract_metrics', 'output', value)
        value['data']['status'] = 'partial'
        self.validator('extract_metrics', 'output').validate(value)

    def test_public_tool_and_architecture_are_aligned(self):
        extraction = next(t for t in self.manifest['tools'] if t['name'] == 'extract_metrics')
        self.assertEqual(extraction['path'], '/api/tasks/{task_id}/extract')
        self.assertTrue(extraction['mutates_revision'])
        self.assertTrue(extraction['requires_idempotency_key'])
        self.assertEqual(set(self.common['extract_metrics_input']['properties']),
                         {'task_id', 'expected_revision', 'metric_ids', 'years'})
        self.assertFalse(list((CONTRACTS / 'examples').glob('save_metric_candidates.*')))
        prompt = (ROOT / 'harness-plugin/prompts/esg-agent.md').read_text()
        self.assertNotIn('save_metric_candidates', prompt)
        for name in TOOLS:
            self.assertIn(name + '(task_id', prompt)
        plan = (ROOT / 'docs/superpowers/plans/2026-11-19-esg-agent-implementation.md').read_text()
        self.assertIn('backend/src/esg_app/', plan)
        self.assertNotIn('\n  src/esg_app/', plan)

    def test_score_dimensions_and_export_formats_are_distinct(self):
        score = self.example('calculate_score', 'success')['data']
        self.assertEqual(len({x['dimension'] for x in score['dimension_results']}), 5)
        export = self.example('export_analysis', 'success')
        self.assertEqual({x['format'] for x in export['data']['artifacts']},
                         {'markdown', 'csv', 'json'})
        self.assertTrue(all(x['revision'] == export['revision']
                            for x in export['data']['artifacts']))
        pending = read(CONTRACTS / 'backend-acceptance-cases.json')
        self.assertEqual(pending['status'], 'pending_backend_implementation_not_executed')
        self.assertEqual(len({c['case_id'] for c in pending['cases']}), 19)

    def test_manifest_references_and_legacy_examples(self):
        for tool in self.manifest['tools']:
            for key in ('input_schema', 'output_schema'):
                path, fragment = tool[key].split('#')
                node = read(CONTRACTS / path)
                for part in fragment.strip('/').split('/'):
                    node = node[part]
                self.assertIsInstance(node, dict)
        legacy = read(CONTRACTS / 'examples/search-evidence.mock.json')
        self.validator('search_evidence', 'output').validate(legacy)
        legacy = read(CONTRACTS / 'examples/error.mock.json')
        self.validator('search_evidence', 'output').validate(legacy)


if __name__ == '__main__':
    unittest.main()
