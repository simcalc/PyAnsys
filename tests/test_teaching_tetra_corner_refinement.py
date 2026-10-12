"""Conservative topology contracts; no native mesh-quality or CFD claims."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import teaching_tetra_corner_refinement as corner
from test_teaching_pump_geometry import cubes


def tetra(height=1.):
    tet = (1, 2, 3, 4)
    return {'points': {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.), 4: (0., 0., height)},
            'tets': [tet], 'patches': {'wall': list(corner.faces(tet))}}


class CornerSubdivisionTests(unittest.TestCase):
    def test_centroid_star_preserves_geometry_roles_volume_and_exact_replay(self):
        raw = tetra(); actual, evidence, end = corner.refine_component(raw, 5, 4)
        self.assertEqual(actual['points'][5], (.25, .25, .25))
        self.assertEqual(actual['patches'], raw['patches'])
        self.assertEqual({n: actual['points'][n] for n in raw['points']}, raw['points'])
        self.assertEqual(end, 6)
        self.assertEqual(len(actual['tets']), 4)
        self.assertTrue(all(corner.volume(t, actual['points']) > 0 for t in actual['tets']))
        self.assertEqual(corner.exterior_inventory(raw), corner.exterior_inventory(actual))
        self.assertAlmostEqual(evidence['originalFluidVolumeM3'], evidence['finalFluidVolumeM3'])
        self.assertEqual(evidence['maximumBoundaryFacesPerCellAfter'], 1)
        original, replay_end = corner.replay_component(actual, evidence)
        self.assertEqual(original, raw)
        self.assertEqual(replay_end, end)

    def test_two_components_preserve_common_interface_and_global_label_intervals(self):
        data = cubes(); threshold = max(n for d in data.values() for n in d['points']); next_node = threshold + 1
        output = {}
        for name, raw in data.items():
            actual, evidence, end = corner.refine_component(raw, next_node, threshold)
            self.assertEqual(evidence['firstInsertedNodeLabel'], next_node)
            self.assertEqual(corner.replay_component(actual, evidence), ({k: raw[k] for k in ('points','tets','patches')}, end))
            self.assertEqual(evidence['additionalCells'], 3 * evidence['splitParentTetraCount'])
            output[name] = actual; next_node = end
        from teaching_pump_geometry import common_interface
        self.assertTrue(common_interface(output['impeller'], output['volute'], [10])['oppositeOutwardWindingVerified'])

    def test_child_cells_with_one_boundary_face_are_not_resplit(self):
        refined, _, end = corner.refine_component(tetra(), 5, 4)
        unchanged, evidence, next_node = corner.refine_component(refined, end, max(refined['points']))
        self.assertEqual(unchanged, refined)
        self.assertEqual(evidence['splitParentTetraCount'], 0)
        self.assertEqual(next_node, end)
        self.assertEqual(corner.replay_component(unchanged, evidence), (refined, end))

    def test_tampered_centroid_topology_roles_metadata_or_claims_reject(self):
        for change in ('centroid','missing_child','child_winding','role','point','volume','quality','CFD','threshold','inserted_interval','hash','extra'):
            actual, evidence, _ = corner.refine_component(tetra(), 5, 4)
            if change == 'centroid': actual['points'][5] = (.24, .25, .25)
            elif change == 'missing_child': actual['tets'].pop()
            elif change == 'child_winding': actual['tets'][0] = (5,3,2,4)
            elif change == 'role': actual['patches']['other'] = actual['patches'].pop('wall')
            elif change == 'point': actual['points'][6] = (0., 0., 0.)
            elif change == 'volume': evidence['finalFluidVolumeM3'] *= 2
            elif change == 'quality': evidence['nativeMeshQualityVerified'] = True
            elif change == 'CFD': evidence['actualCfdRunVerified'] = True
            elif change == 'threshold': evidence['originalGlobalMaximumNodeLabel'] = 5
            elif change == 'inserted_interval': evidence['firstInsertedNodeLabel'] = 6
            elif change == 'hash': evidence['originalConnectivitySha256'] = 'a'*64
            else: evidence['extra'] = 'unsupported'
            with self.subTest(change=change), self.assertRaises(ValueError): corner.replay_component(actual, evidence)

    def test_nonmanifold_missing_boundary_duplicate_face_and_negative_cells_reject(self):
        for change in ('nonmanifold','missing_boundary','duplicate_boundary','negative','zero','label_collision'):
            raw = tetra()
            if change == 'nonmanifold': raw['tets'] *= 3
            elif change == 'missing_boundary': raw['patches']['wall'].pop()
            elif change == 'duplicate_boundary': raw['patches']['wall'].append(raw['patches']['wall'][0])
            elif change == 'negative': raw['tets'] = [(2,1,3,4)]
            elif change == 'zero': raw['points'][4] = (0.,0.,0.)
            with self.subTest(change=change), self.assertRaises(ValueError): corner.refine_component(raw, 4 if change == 'label_collision' else 5, 4)

    def test_flattened_positive_tetra_is_not_claimed_as_native_quality(self):
        actual, evidence, _ = corner.refine_component(tetra(1e-8), 5, 4)
        self.assertTrue(all(corner.volume(t, actual['points']) > 0 for t in actual['tets']))
        self.assertFalse(evidence['nativeMeshQualityVerified'])
        self.assertFalse(evidence['actualCfdRunVerified'])
        self.assertFalse(evidence['mainApplicationWrites'])
        self.assertEqual(corner.replay_component(actual, evidence)[0], tetra(1e-8))


if __name__ == '__main__': unittest.main()
