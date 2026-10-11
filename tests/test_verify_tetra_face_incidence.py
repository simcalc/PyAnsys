"""Synthetic topology/parser fixtures only; these tests are not CFD evidence."""
from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import verify_tetra_face_incidence as gate

TETRA=np.asarray([[1,2,3,4]],dtype="u8")
SURFACE=np.asarray([[1,3,2],[1,2,4],[2,3,4],[3,1,4]],dtype="u8")


def exterior(tetra):
    faces=np.asarray(tetra,dtype="u8")[:,[(0,2,1),(0,1,3),(1,2,3),(2,0,3)]].reshape(-1,3)
    keys=np.sort(faces,axis=1);unique,counts=np.unique(keys,axis=0,return_counts=True)
    return np.asarray([face for face,key in zip(faces,keys) if counts[np.flatnonzero(np.all(unique==key,axis=1))[0]]==1],dtype="u8")


def msh(tetra=TETRA,surface=SURFACE):
    coordinates={1:(0,0,0),2:(1,0,0),3:(0,1,0),4:(0,0,1),5:(0,0,-1),6:(0,0,2)}
    nodes=sorted(set(np.asarray(tetra).reshape(-1))|set(np.asarray(surface).reshape(-1)))
    rows=["$MeshFormat","2.2 0 8","$EndMeshFormat","$PhysicalNames","2",'2 3 "source-wall"','3 1 "fluid"',"$EndPhysicalNames","$Nodes",str(len(nodes))]
    rows += [str(v)+" "+" ".join(str(c) for c in coordinates[v]) for v in nodes]
    rows += ["$EndNodes","$Elements",str(len(tetra)+len(surface))]
    rows += [str(i+1)+" 2 2 3 1 "+" ".join(str(v) for v in row) for i,row in enumerate(surface)]
    rows += [str(i+1+len(surface))+" 4 2 1 1 "+" ".join(str(v) for v in row) for i,row in enumerate(tetra)]
    return ("\n".join(rows+["$EndElements",""])).encode("ascii")


class ExactTetraIncidenceContracts(unittest.TestCase):
    def test_one_tetra_all_exterior_faces_exactly_once(self):
        result=gate.audit_incidence(TETRA,SURFACE)
        self.assertTrue(result["accepted"]);self.assertEqual(result["incidenceMultiplicityCounts"],{"1":4})
        self.assertTrue(result["allActualTetraFacesAudited"]);self.assertFalse(result["actualCfdRunVerified"])
        self.assertFalse(result["nativeOpenFOAMQualityVerified"]);self.assertFalse(result["geometricSelfIntersectionChecked"])

    def test_reversed_source_surface_winding_is_not_a_false_failure(self):
        self.assertTrue(gate.audit_incidence(TETRA,SURFACE[:,::-1])["accepted"])

    def test_two_positive_order_tetra_opposite_shared_face(self):
        tetra=np.asarray([[1,2,3,4],[1,3,2,5]],dtype="u8")
        result=gate.audit_incidence(tetra,exterior(tetra))
        self.assertTrue(result["accepted"]);self.assertEqual(result["incidenceMultiplicityCounts"],{"1":6,"2":1})
        self.assertEqual(result["twoIncidenceOrientationConflictCount"],0)

    def test_missing_exterior_tag_is_measured_not_filled(self):
        result=gate.audit_incidence(TETRA,SURFACE[:-1])
        self.assertFalse(result["accepted"]);self.assertEqual(result["missingTaggedSurfaceForExteriorFaceCount"],1)
        self.assertEqual(result["samples"]["missingExteriorTag"][0]["tetraElementIds"],[1])

    def test_extra_tagged_interior_face_rejected(self):
        tetra=np.asarray([[1,2,3,4],[1,3,2,5]],dtype="u8")
        result=gate.audit_incidence(tetra,np.vstack((exterior(tetra),np.asarray([1,2,3],dtype="u8"))))
        self.assertFalse(result["accepted"]);self.assertEqual(result["taggedSurfaceWithoutSingleVolumeIncidenceCount"],1)
        self.assertEqual(result["samples"]["taggedNotExterior"][0]["volumeIncidenceCount"],2)

    def test_same_side_cells_positive_order_still_have_orientation_conflict(self):
        tetra=np.asarray([[1,2,3,4],[1,2,3,6]],dtype="u8")
        result=gate.audit_incidence(tetra,exterior(tetra),tetra_element_ids=[23,55])
        self.assertFalse(result["accepted"]);self.assertEqual(result["twoIncidenceOrientationConflictCount"],1)
        self.assertEqual(result["higherThanTwoIncidenceFaceCount"],0)
        self.assertEqual(result["samples"]["orientationConflict"][0]["tetraElementIds"],[23,55])

    def test_three_incident_tetra_face_cannot_be_admitted(self):
        tetra=np.asarray([[1,2,3,4],[1,3,2,5],[1,2,3,6]],dtype="u8")
        result=gate.audit_incidence(tetra,exterior(tetra))
        self.assertFalse(result["accepted"]);self.assertEqual(result["higherThanTwoIncidenceFaceCount"],1)
        self.assertEqual(result["incidenceMultiplicityCounts"],{"1":9,"3":1})

    def test_duplicate_tetra_or_surface_cannot_be_deduplicated(self):
        result=gate.audit_incidence(np.vstack((TETRA,TETRA)),SURFACE)
        self.assertFalse(result["accepted"]);self.assertEqual(result["duplicateTetraCount"],1)
        result=gate.audit_incidence(TETRA,np.vstack((SURFACE,SURFACE[0])))
        self.assertFalse(result["accepted"]);self.assertEqual(result["duplicateTaggedSurfaceTriangleCount"],1)

    def test_repeated_vertices_negative_float_boolean_and_bad_identifier_reject(self):
        result=gate.audit_incidence([[1,2,2,4]],SURFACE);self.assertFalse(result["accepted"]);self.assertEqual(result["repeatedNodeTetraCount"],1)
        result=gate.audit_incidence(TETRA,[[1,1,2]]);self.assertFalse(result["accepted"]);self.assertEqual(result["repeatedNodeSurfaceTriangleCount"],1)
        for rows in ([[1,2,3,-1]],TETRA.astype("f8"),TETRA.astype(bool)):
            with self.subTest(rows=rows),self.assertRaises(gate.TetraIncidenceIssue):gate.audit_incidence(rows,SURFACE)
        with self.assertRaises(gate.TetraIncidenceIssue):gate.audit_incidence(TETRA,SURFACE,tetra_element_ids=[0])

    def test_sparse_large_labels_compact_without_key_collision(self):
        values=np.asarray([17,500_000_000,2**63,2**63+100],dtype="u8")
        result=gate.audit_incidence(values[TETRA-1],values[SURFACE-1])
        self.assertTrue(result["accepted"]);self.assertEqual(result["actualReferencedNodeCount"],4)

    def test_array_budget_is_explicit(self):
        with patch.object(gate,"MAX_CELLS",1),self.assertRaises(gate.TetraIncidenceIssue):gate.audit_incidence(np.vstack((TETRA,TETRA)),SURFACE)
        with patch.object(gate,"MAX_NODES",3),self.assertRaises(gate.TetraIncidenceIssue):gate.audit_incidence(TETRA,SURFACE)

    def test_diagnostic_incidence_samples_are_bounded_without_losing_counts(self):
        result=gate.audit_incidence(np.repeat(TETRA,20,axis=0),SURFACE)
        sample=result["samples"]["nonmanifold"][0]
        self.assertEqual(sample["incidenceCount"],20);self.assertEqual(len(sample["tetraElementIds"]),gate.MAX_SAMPLES)
        self.assertTrue(sample["incidenceSampleTruncated"])
        result=gate.audit_incidence(TETRA,np.repeat(SURFACE,20,axis=0))
        self.assertEqual(result["duplicateTaggedSurfaceTriangleCount"],76)

    def test_actual_plain_msh_hash_read_only_and_no_native_claim(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"synthetic.msh";raw=msh();path.write_bytes(raw);result=gate.verify_msh(path)
            self.assertTrue(result["accepted"]);self.assertTrue(result["finitePositiveSignedTetraVolumeVerified"])
            self.assertEqual(result["meshFile"],{"name":"synthetic.msh","bytes":len(raw),"sha256":sha256(raw).hexdigest()})
            self.assertEqual(path.read_bytes(),raw)

    def test_actual_file_rejection_retains_measured_topology_report(self):
        tetra=np.asarray([[1,2,3,4],[1,2,3,6]],dtype="u8")
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"synthetic.msh";path.write_bytes(msh(tetra,exterior(tetra)))
            with self.assertRaises(gate.TetraIncidenceIssue) as caught:gate.verify_msh(path)
            self.assertEqual(caught.exception.report["twoIncidenceOrientationConflictCount"],1)
            self.assertTrue(caught.exception.report["finitePositiveSignedTetraVolumeVerified"])

    def test_malformed_count_node_reference_nonpositive_and_trailing_sections_reject(self):
        mutations=[msh().replace(b"$Nodes\n4\n",b"$Nodes\n5\n"),msh().replace(b"4 4 2 1 1 1 2 3 4",b"4 4 2 1 1 1 2 3 9"),
            msh(np.asarray([[1,3,2,4]]),SURFACE),msh()+b"$Extra\n0\n$EndExtra\n",msh().replace(b"1 0 0 0",b"1 nan 0 0")]
        # The tetra element follows the four surface elements, hence ID5.
        mutations[1]=msh().replace(b"5 4 2 1 1 1 2 3 4",b"5 4 2 1 1 1 2 3 9")
        for raw in mutations:
            with self.subTest(raw=raw[-60:]),TemporaryDirectory() as tmp:
                path=Path(tmp)/"synthetic.msh";path.write_bytes(raw)
                with self.assertRaises((gate.TetraIncidenceIssue,ValueError)):gate.verify_msh(path)

    def test_file_and_line_bounds_before_allocation(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"synthetic.msh";path.write_bytes(msh())
            with patch.object(gate,"MAX_FILE_BYTES",10),self.assertRaises(gate.TetraIncidenceIssue):gate.verify_msh(path)
            with patch.object(gate,"MAX_LINE_BYTES",4),self.assertRaises(gate.TetraIncidenceIssue):gate.verify_msh(path)


if __name__=="__main__":unittest.main()
