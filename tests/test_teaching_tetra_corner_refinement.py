"""Conservative topology contracts; no native mesh-quality or CFD claims."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import teaching_tetra_corner_refinement as corner
from test_teaching_pump_geometry import cubes


def tetra(height=1.):
    tet = (1, 2, 3, 4)
    return {'points': {1: (0., 0., 0.), 2: (1., 0., 0.), 3: (0., 1., 0.), 4: (0., 0., height)},
            'tets': [tet], 'patches': {'wall': list(corner.faces(tet))}}


def transition_pair(height=.12):
    """Synthetic two-tet face: small side is subdivided prospectively, large side is not."""
    raw=tetra(height);raw['points'][5]=(0.,0.,-1.);raw['tets'].append((1,3,2,5))
    counts=corner.Counter(tuple(sorted(f)) for t in raw['tets'] for f in corner.faces(t))
    raw['patches']={'wall':[f for t in raw['tets'] for f in corner.faces(t) if counts[tuple(sorted(f))]==1]}
    return raw


class CornerSubdivisionTests(unittest.TestCase):
    def test_default_recipe_and_evidence_golden_bytes_remain_unchanged(self):
        actual,evidence,_=corner.refine_component(tetra(),5,4)
        self.assertNotIn('transitionBalancing',evidence)
        self.assertEqual(sha256(corner.encoded(evidence)).hexdigest(),'4cdcf9c2fb9c05877e8c45e227dde213d44b1f721fa64ef6863a9639ba7ef06e')
        self.assertEqual(sha256(corner.encoded(actual)).hexdigest(),'aa841b3741528820c012a90df7ab8a101d3208f2a5fbfa2f4ab93e78d1afe5c5')

    def test_two_tet_normal_distance_transition_selects_only_large_unsplit_parent(self):
        raw=transition_pair();snapshot=deepcopy(raw)
        pairs=corner.internal_distances(raw)
        self.assertEqual(len(pairs),1)
        _,_,key,small,large=pairs[0]
        self.assertEqual(key,(1,2,3));self.assertAlmostEqual(small,.12/4);self.assertAlmostEqual(large,1/4)
        selected,proof=corner.transition_selection(raw,{0})
        self.assertEqual(selected,{0,1});self.assertEqual(proof['additionalParentRowsOneBasedSample'],[2])
        self.assertEqual(proof['closurePasses'],1)
        self.assertAlmostEqual(proof['minimumProspectiveWeightBeforeBalancing'],.03/1.03)
        self.assertAlmostEqual(proof['minimumProspectiveWeightAfterBalancing'],.12/1.12)
        self.assertEqual(proof['prospectiveLowWeightFaceCountAfterBalancing'],0)
        self.assertEqual(raw,snapshot);self.assertFalse(proof['nativeInterpolationWeightVerified'])
        self.assertFalse(proof['nativeMeshQualityVerified']);self.assertFalse(proof['actualCfdRunVerified'])

    def test_lean_all_derived_faces_exact_replay_and_no_source_mutation(self):
        raw=transition_pair();snapshot=deepcopy(raw)
        actual,proof,end=corner.refine_component(raw,6,5,balance_transitions=True)
        self.assertEqual(raw,snapshot);self.assertEqual(len(actual['tets']),8);self.assertEqual(end,8)
        self.assertEqual(actual['patches'],raw['patches'])
        balancing=proof['transitionBalancing'];self.assertTrue(balancing['allDerivedInternalFacePlanesChecked'])
        self.assertEqual(balancing['finalInternalFaceCount'],13)
        self.assertGreaterEqual(balancing['minimumFinalInternalFaceWeight'],.05)
        self.assertEqual(balancing['finalGeometricLowWeightFaceCount'],0)
        self.assertEqual(corner.replay_component(actual,proof),(raw,end))

    def test_unresolvable_original_weight_no_repeated_split_or_threshold_lowering(self):
        raw=transition_pair(.01);snapshot=deepcopy(raw)
        with self.assertRaisesRegex(ValueError,'unchanged .05'):corner.transition_selection(raw,{0})
        with self.assertRaisesRegex(ValueError,'unchanged .05'):corner.refine_component(raw,6,5,balance_transitions=True)
        self.assertEqual(raw,snapshot)

    def test_balancing_caps_and_explicit_opt_in_fail_before_source_changes(self):
        for key,value in (('maximumOriginalCells',1),('maximumFinalCells',7),('maximumFinalPoints',6),('maximumClosurePasses',0)):
            raw=transition_pair();snapshot=deepcopy(raw)
            with self.subTest(key=key),mock.patch.dict(corner.BALANCING_LIMITS,{key:value}),self.assertRaises(ValueError):
                corner.transition_selection(raw,{0})
            self.assertEqual(raw,snapshot)
        for value in (1,'yes',None):
            with self.subTest(value=value),self.assertRaises(ValueError):corner.refine_component(tetra(),5,4,balance_transitions=value)

    def test_balancing_changed_selection_limits_threshold_and_claims_fail_exact_replay(self):
        for change in ('weight','limits','selection','sample','minimum','claim','typed','extra'):
            actual,evidence,_=corner.refine_component(transition_pair(),6,5,balance_transitions=True)
            proof=evidence['transitionBalancing']
            if change=='weight':proof['minimumInterpolationWeight']=.01
            elif change=='limits':proof['limits']['maximumFinalCells']+=1
            elif change=='selection':proof['selectedParentRowsOneBasedSha256']='a'*64
            elif change=='sample':proof['additionalParentRowsOneBasedSample']=[1]
            elif change=='minimum':proof['minimumFinalInternalFaceWeight']=.5
            elif change=='claim':proof['nativeMeshQualityVerified']=True
            elif change=='typed':proof['enabled']=1
            else:proof['extra']=True
            with self.subTest(change=change),self.assertRaises(ValueError):corner.replay_component(actual,evidence)

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
