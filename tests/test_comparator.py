"""Regression tests against explicit CSV scenarios and uploaded-file behavior."""
import io
import json
import unittest
from pathlib import Path
import pandas as pd
from src.csv_comp import DataComparator
from config import DATA


class TestCsvScenarios(unittest.TestCase):
    pass


def make_case(case):
    def test(self):
        folder = DATA / case['folder']
        comparator = DataComparator('client_id')
        comparator.set_source(folder / 'source.csv')
        comparator.add_target(case['target'], folder / case['target'])
        expected = case['expected']
        if 'error' in expected:
            result = comparator.compare_all()[case['target']]
            self.assertEqual(result['status'], 'error')
            self.assertIn(expected['error'], result['message'])
            return
        result = comparator.compare_target(case['target'])
        for key, value in expected.items():
            if key == 'changes':
                self.assertEqual(result['modified_values'].to_dict('records'), value)
            elif key in ('missing_columns', 'extra_columns'):
                self.assertEqual(result[key], value)
            else:
                self.assertEqual(result['summary'][key], value)
        self.assertEqual(result['missing_rows']['client_id'].tolist(),
                         comparator.source.loc[~comparator.source.client_id.isin(comparator.targets[case['target']].client_id), 'client_id'].tolist())
    return test


for case in json.loads((DATA / 'cases.json').read_text()):
    name = f"test_{case['folder']}_{Path(case['target']).stem}"
    setattr(TestCsvScenarios, name, make_case(case))


class TestComparatorBehavior(unittest.TestCase):
    def setUp(self):
        self.comparator = DataComparator('client_id')
        self.source = DATA / 'donnee1/source.csv'

    def test_uploaded_file_can_be_read_twice(self):
        upload = io.BytesIO(self.source.read_bytes())
        self.comparator.set_source(upload)
        self.comparator.add_target('same', upload)
        self.assertTrue(self.comparator.compare_target('same')['summary']['identical'])
        self.assertEqual(self.comparator.source.client_id.iloc[0], '001')

    def test_invalid_target_does_not_block_valid_target(self):
        self.comparator.set_source(self.source)
        self.comparator.add_target('bad', DATA / 'donnee5/target_duplicate.csv')
        self.comparator.add_target('good', self.source)
        result = self.comparator.compare_all()
        self.assertEqual(result['bad']['status'], 'error')
        self.assertTrue(result['good']['comparison']['summary']['identical'])

    def test_missing_source(self):
        with self.assertRaisesRegex(ValueError, 'Load a source'):
            self.comparator.compare_all()

    def test_no_targets(self):
        self.comparator.set_source(self.source)
        with self.assertRaisesRegex(ValueError, 'at least one target'):
            self.comparator.compare_all()

    def test_unknown_target(self):
        self.comparator.set_source(self.source)
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.comparator.compare_target('unknown')

    def test_duplicate_target_name(self):
        self.comparator.add_target('same', self.source)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.comparator.add_target('same', self.source)

    def test_remove_target(self):
        self.comparator.add_target('same', self.source)
        self.comparator.remove_target('same')
        self.assertNotIn('same', self.comparator.targets)
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.comparator.remove_target('same')

    def test_invalid_source_preserves_previous_source(self):
        self.comparator.set_source(self.source)
        previous = self.comparator.source.copy()
        with self.assertRaises(ValueError):
            self.comparator.set_source(DATA / 'donnee5/target_duplicate.csv')
        pd.testing.assert_frame_equal(previous, self.comparator.source)

    def test_zero_byte_csv_is_rejected(self):
        with self.assertRaises(pd.errors.EmptyDataError):
            self.comparator.add_target('empty', io.BytesIO(b''))

    def test_missing_and_added_identifiers(self):
        self.comparator.set_source(self.source)
        self.comparator.add_target('mixed', DATA / 'donnee2/target_mixed.csv')
        result = self.comparator.compare_target('mixed')
        self.assertEqual(result['missing_rows'].client_id.tolist(), ['003'])
        self.assertEqual(result['added_rows'].client_id.tolist(), ['004'])


if __name__ == '__main__':
    unittest.main()
