"""Synthetic full-volume conversion fixtures; no fixture is pump/solver evidence."""
from copy import deepcopy
from hashlib import sha256
import math
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import convert_frozen_rotor_native_h5 as converter
import numpy as np
try:
    import h5py
except (ImportError,OSError):
    h5py=None


def source_pin(component):
    name=converter.COMPONENTS[component][0];size,digest=converter.inventory.FILES[name]
    return {"name":name,"bytes":size,"sha256":digest,"sourceUrl":converter.inventory.mesh_url(name)}


def volume_model(points,faces,kind=2,component="impeller"):
    """Orient a convex synthetic cell inward, matching stored Fluent c0."""
    xyz=np.asarray(points,dtype="f8");centre=xyz.mean(axis=0);stored=[]
    for face in faces:
        face=list(face);v=xyz[face];normal=np.cross(v[1]-v[0],v[2]-v[0])
        if np.dot(normal,v.mean(axis=0)-centre)>0:face.reverse()
        stored.append(face)
    return assembled(xyz,stored,[1]*len(stored),[0]*len(stored),[kind],component)


def assembled(points,faces,c0,c1,kinds,component="impeller"):
    widths=np.asarray([len(row) for row in faces],dtype="u2");offsets=np.r_[0,np.cumsum(widths,dtype="i8")]
    inside=sum(a>0 and b>0 for a,b in zip(c0,c1));zones=[]
    if inside:zones.append({"name":"interior-original","zoneType":2,"zoneId":4,"minimumId":1,"maximumId":inside})
    zones.append({"name":"wall-original","zoneType":3,"zoneId":3,"minimumId":inside+1,"maximumId":len(faces)})
    return {"component":component,"coordinates":np.asarray(points,dtype="f8"),"faceWidths":widths,"faceOffsets":offsets,
        "faceNodes":np.asarray([v for row in faces for v in row],dtype="u4"),"c0":np.asarray(c0,dtype="u4"),"c1":np.asarray(c1,dtype="u4"),
        "cellTypes":np.asarray(kinds,dtype="u1"),"cellCount":len(kinds),"nodeCount":len(points),"faceCount":len(faces),
        "faceZones":zones,"sourceFile":source_pin(component)}


def tetra(component="impeller"):
    return volume_model([(0,0,0),(1,0,0),(0,1,0),(0,0,1)],[(0,1,2),(0,1,3),(1,2,3),(2,0,3)],component=component)


def twin_tetra():
    points=np.asarray([(0,0,0),(1,0,0),(0,1,0),(0,0,1),(0,0,-1)],dtype="f8")
    a=tetra();b=volume_model(points[[0,1,2,4]],[(0,1,2),(0,1,3),(1,2,3),(2,0,3)])
    fa=[a["faceNodes"][a["faceOffsets"][i]:a["faceOffsets"][i+1]].tolist() for i in range(4)]
    fb=[[int([0,1,2,4][v]) for v in b["faceNodes"][b["faceOffsets"][i]:b["faceOffsets"][i+1]]] for i in range(4)]
    return assembled(points,[fa[0],*fa[1:],*fb[1:]],[1]*4+[2]*3,[2]+[0]*6,[2,2])


def counts(model):
    return {**{key:model[key] for key in ("cellCount","nodeCount","faceCount")},
            "internalFaceCount":int(np.count_nonzero((model["c0"]>0)&(model["c1"]>0)))}


class OriginalVolumeContracts(unittest.TestCase):
    def test_positive_closed_tetra_and_exact_c0_boundary_reversal(self):
        model=tetra();result=converter.audit_cells(model)
        self.assertAlmostEqual(result["minimumCellVolumeAssumedM3"],1/6)
        self.assertEqual(result["originalCellCountChecked"],1)
        self.assertTrue(result["directedEdgeIncidenceClosed"])
        self.assertFalse(result["meshQualityCheckedBySolver"])
        self.assertFalse(result["nativeCoordinateUnitsVerified"])
        order,owner,neighbour,reverse,patches=converter.face_order(model)
        np.testing.assert_array_equal(order,np.arange(4));np.testing.assert_array_equal(owner,np.zeros(4))
        self.assertEqual(len(neighbour),0);self.assertTrue(reverse.all());self.assertEqual(patches[0]["type"],"wall")

    def test_both_adjacency_orders_and_two_original_cells(self):
        for swapped in (False,True):
            model=twin_tetra()
            if swapped:
                model["c0"][0],model["c1"][0]=2,1;model["faceNodes"][:3]=model["faceNodes"][:3][::-1]
            result=converter.audit_cells(model);self.assertAlmostEqual(result["totalCellVolumeAssumedM3"],1/3)
            order,owner,neighbour,reverse,patches=converter.face_order(model)
            self.assertEqual((int(owner[0]),int(neighbour[0])),(0,1))
            self.assertEqual(bool(reverse[0]),not swapped);self.assertEqual(patches[0]["startFace"],1)

    def test_zero_on_c0_preserves_outward_boundary_winding(self):
        model=tetra();model["c0"][:]=0;model["c1"][:]=1
        for i in range(model["faceCount"]):
            lo,hi=model["faceOffsets"][i:i+2];model["faceNodes"][lo:hi]=model["faceNodes"][lo:hi][::-1]
        self.assertAlmostEqual(converter.audit_cells(model)["totalCellVolumeAssumedM3"],1/6)
        self.assertFalse(converter.face_order(model)[3].any())

    def test_hex_quad_faces_retained_and_volume_one(self):
        model=volume_model([(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)],
            [(0,1,2,3),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)],4)
        result=converter.audit_cells(model);self.assertAlmostEqual(result["totalCellVolumeAssumedM3"],1)
        with TemporaryDirectory() as tmp:
            row=converter.write_mesh(Path(tmp),model,result,[0]);text=(Path(tmp)/row["casePath"] /"constant/polyMesh/faces").read_text()
            self.assertEqual(len(__import__("re").findall(r"(?m)^4\(",text)),6)
            self.assertTrue(row["checks"]["originalPolygonsRetained"])

    def test_mixed_shapes_pyramid_prism_and_polyhedron(self):
        cases=[(5,[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(.5,.5,1)],[(0,1,2,3),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],1/3),
            (6,[(0,0,0),(1,0,0),(0,1,0),(0,0,1),(1,0,1),(0,1,1)],[(0,1,2),(3,4,5),(0,1,4,3),(1,2,5,4),(2,0,3,5)],.5)]
        for kind,points,faces,volume in cases:
            with self.subTest(kind=kind):self.assertAlmostEqual(converter.audit_cells(volume_model(points,faces,kind))["totalCellVolumeAssumedM3"],volume)
        model=tetra();model["cellTypes"][:]=7
        self.assertAlmostEqual(converter.audit_cells(model)["totalCellVolumeAssumedM3"],1/6)

    def test_per_cell_chunk_boundary_is_not_assumed_one_cell(self):
        with patch.object(converter,"AUDIT_CELLS",1):
            self.assertEqual(converter.audit_cells(twin_tetra())["originalCellCountChecked"],2)

    def test_wrong_winding_missing_face_and_wrong_shape_reject(self):
        model=tetra();model["faceNodes"][:3]=model["faceNodes"][:3][::-1]
        with self.assertRaisesRegex(converter.ConversionIssue,"opposite"):converter.audit_cells(model)
        model=tetra();model["faceNodes"]=model["faceNodes"][::-1].copy()
        with self.assertRaises(converter.ConversionIssue):converter.audit_cells(model)
        model=tetra();model["cellTypes"][:]=4
        with self.assertRaisesRegex(converter.ConversionIssue,"incidence"):converter.audit_cells(model)

    def test_negative_total_winding_rejects_positive_volume_claim(self):
        model=tetra()
        for i in range(model["faceCount"]):
            lo,hi=model["faceOffsets"][i:i+2];model["faceNodes"][lo:hi]=model["faceNodes"][lo:hi][::-1]
        with self.assertRaisesRegex(converter.ConversionIssue,"positive"):converter.audit_cells(model)

    def test_plain_ascii_exact_native_roles_and_face_id_map(self):
        model=twin_tetra();model["coordinates"]+=.12345678901234567
        with TemporaryDirectory() as tmp:
            root=Path(tmp);row=converter.write_mesh(root,model,converter.audit_cells(model),[0]);mesh=root/row["casePath"]/"constant/polyMesh"
            n,body=converter.guards.base.native_list(mesh/"points")
            points=np.asarray([[float(v) for v in part.split()] for part in __import__("re").findall(r"\(([^()]*)\)",body)])
            self.assertEqual(n,model["nodeCount"]);np.testing.assert_array_equal(points,model["coordinates"])
            for key in converter.MESH_FILES:converter.guards.base.native_list(mesh/key)
            self.assertEqual((root/row["sourceFaceIdMapFile"]["path"]).read_bytes().split(b"(\n",1)[1].split(b")",1)[0].split(),[str(i).encode() for i in range(1,8)])
            for file in row["nativeFiles"]+[row["sourceFaceIdMapFile"]]:
                raw=(root/file["path"]).read_bytes();self.assertEqual(file["sha256"],sha256(raw).hexdigest());self.assertEqual(file["bytes"],len(raw))

    def test_writer_exclusive_and_bounded_before_bytes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);writer=converter.Writer(root,"fresh/points",[],[0])
            try:
                with patch.object(converter,"MAX_FILE",3),self.assertRaises(converter.ConversionIssue):writer.write("four")
                self.assertEqual(writer.path.stat().st_size,0)
            finally:writer.close()
            with self.assertRaises(FileExistsError):converter.Writer(root,"fresh/points",[],[0])
            with self.assertRaises(ValueError):converter.Writer(root,"../escape",[],[0])

    def _converted(self,root):
        sources=[source_pin(name) for name in converter.COMPONENTS];models={name:tetra(name) for name in converter.COMPONENTS}
        with patch.dict(os.environ,{"RUNNER_TEMP":str(root)}),patch.object(converter.inventory,"verify_file",side_effect=lambda path,name:source_pin(name.split(".")[0])), \
                patch.object(converter,"read_mesh",side_effect=lambda path,name:deepcopy(models[name])):
            inputs=root/"inputs";inputs.mkdir();report=converter.convert(inputs,root/"converted")
        return report,models

    def test_conversion_provenance_and_current_output_verifier(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);report,models=self._converted(root)
            with patch.object(converter,"SOURCE_COUNTS",{key:counts(value) for key,value in models.items()}):
                checked=converter.verify_output(root/"converted")
            self.assertEqual(checked["report"],report);self.assertEqual(checked["components"][0]["cellZone"],"rotating-fluid")
            for key in ("actualCfdRunVerified","meshQualityCheckedBySolver","simulationExecuted","studentLicenceRouteUsed","mainApplicationWrites"):
                self.assertIs(report[key],False)
            self.assertEqual(report["coordinateUnits"],"UNVERIFIED")

    def test_changed_output_missing_map_and_false_claim_rejected(self):
        for fault in ("changed-bytes","missing-map","native-claim","source-pin","cell-count"):
            with self.subTest(fault=fault),TemporaryDirectory() as tmp:
                root=Path(tmp);report,models=self._converted(root);out=root/"converted"
                if fault=="changed-bytes":(out/"impeller-case/constant/polyMesh/points").write_bytes(b"corrupted")
                elif fault=="missing-map":(out/"volute-case/sourceFaceIds").unlink()
                else:
                    if fault=="native-claim":report["actualCfdRunVerified"]=True
                    if fault=="source-pin":report["components"][0]["sourceFile"]["sha256"]="0"*64
                    if fault=="cell-count":report["components"][0]["cellCount"]+=1
                    (out/"native-conversion.json").write_bytes(converter.encoded(report))
                with patch.object(converter,"SOURCE_COUNTS",{key:counts(value) for key,value in models.items()}),self.assertRaises(ValueError):converter.verify_output(out)

    def test_invalid_destination_precedes_source_read(self):
        with TemporaryDirectory() as tmp,patch.dict(os.environ,{"RUNNER_TEMP":tmp}),patch.object(converter.inventory,"verify_file",side_effect=AssertionError("source read")):
            root=Path(tmp);inputs=root/"inputs";inputs.mkdir()
            with self.assertRaises(ValueError):converter.convert(inputs,root.parent/"outside-conversion")
            with self.assertRaises(ValueError):converter.convert(inputs,inputs)

    def test_failure_rechecks_both_sources_and_publishes_no_success(self):
        with TemporaryDirectory() as tmp,patch.dict(os.environ,{"RUNNER_TEMP":tmp}):
            root=Path(tmp);inputs=root/"inputs";inputs.mkdir()
            with patch.object(converter.inventory,"verify_file",side_effect=lambda path,name:source_pin(name.split(".")[0])) as verify, \
                    patch.object(converter,"read_mesh",side_effect=converter.ConversionIssue("broken native topology")):
                with self.assertRaises(converter.ConversionIssue):converter.convert(inputs,root/"failed")
            self.assertEqual(verify.call_count,4)
            self.assertFalse((root/"failed/native-conversion.json").exists())


@unittest.skipIf(h5py is None,"h5py unavailable under local Windows policy; native contracts run on standard GitHub Ubuntu")
class FullHdf5SectionContracts(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/"synthetic-full-volume.h5";self.model=tetra()
        self.create()

    def create(self):
        model=self.model
        with h5py.File(self.path,"w") as handle:
            mesh=handle.require_group("meshes/1")
            for key in ("cellCount","nodeCount","faceCount"):mesh.attrs[key]=np.asarray([model[key]],dtype="u8")
            for key in ("cellOffset","nodeOffset","faceOffset"):mesh.attrs[key]=np.asarray([0],dtype="u8")
            mesh.attrs["dimension"]=np.asarray([3],dtype="i4")
            for name,total,ids,lows,highs,names in (("cells",1,[36],[1],[1],["impeller"]),("nodes",4,[1,2],[1,3],[2,4],["first","second"]),("faces",4,[3],[1],[4],["wall-original"])):
                group=mesh.require_group(name+"/zoneTopology");group.attrs["nZones"]=[len(ids)]
                for key,value in (("id",ids),("minId",lows),("maxId",highs)):group[key]=np.asarray(value,dtype="i8")
                group["name"]=np.asarray([(";".join(names)+";").encode()])
                if name=="cells":group["childZoneId"]=[0]
                if name=="faces":
                    for key,value in (("zoneType",[3]),("faceType",[3]),("childZoneId",[0]),("shadowZoneId",[0])):group[key]=value
            for number,lo,hi in (("9",1,2),("2",3,4)):
                d=mesh.require_group("nodes/coords").create_dataset(number,data=model["coordinates"][lo-1:hi]);d.attrs["minId"]=[lo];d.attrs["maxId"]=[hi]
            ctype=mesh.require_group("cells/ctype");ctype.attrs["nSections"]=[1];section=ctype.create_group("1")
            section.attrs["minId"]=[1];section.attrs["maxId"]=[1];section.attrs["elementType"]=[0];section["cell-types"]=np.asarray([2],dtype="i2")
            for key in ("nodes","c0","c1"):mesh.require_group("faces/"+key).attrs["nSections"]=[2]
            for number,lo,hi in (("8",1,2),("3",3,4)):
                g=mesh["faces/nodes"].create_group(number);g.attrs["minId"]=[lo];g.attrs["maxId"]=[hi]
                g["nnodes"]=model["faceWidths"][lo-1:hi];g["nodes"]=model["faceNodes"][model["faceOffsets"][lo-1]:model["faceOffsets"][hi]]+1
                for key in ("c0","c1"):
                    d=mesh["faces/"+key].create_dataset(number,data=model[key][lo-1:hi]);d.attrs["minId"]=[lo];d.attrs["maxId"]=[hi]

    def read(self):
        with patch.object(converter.inventory,"verify_file",return_value=source_pin("impeller")),patch.object(converter,"SOURCE_COUNTS",{"impeller":counts(self.model)}):
            return converter.read_mesh(self.path,"impeller")

    def test_multiple_section_numbers_native_mixed_type_and_immutable_file(self):
        before=sha256(self.path.read_bytes()).hexdigest();model=self.read()
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(),before)
        np.testing.assert_array_equal(model["coordinates"],self.model["coordinates"])
        np.testing.assert_array_equal(model["faceNodes"],self.model["faceNodes"])
        self.assertEqual(model["cellTypes"].tolist(),[2]);self.assertAlmostEqual(converter.audit_cells(model)["totalCellVolumeAssumedM3"],1/6)

    def test_bad_adjacency_section_count_and_vertex_rejected(self):
        mutations=[("faces/c0/8","data",[0,1]),("faces/c1/8","data",[2,0]),("faces/nodes/8/nodes","data",[1,1,2,1,2,4]),
            ("faces/c0/3","minId",[2]),("cells/ctype/1","maxId",[2]),("nodes/coords/2","data",[[math.nan,1,0],[0,0,1]])]
        for name,mode,value in mutations:
            with self.subTest(name=name):
                self.create()
                with h5py.File(self.path,"r+") as handle:
                    obj=handle["meshes/1/"+name]
                    if mode=="data":obj[:]=value
                    else:obj.attrs[mode]=value
                with self.assertRaises(ValueError):self.read()

    def test_connectivity_budget_checked_before_payload(self):
        read_names=[];original=h5py.Dataset.__getitem__
        def tracked(dataset,key):read_names.append(dataset.name);return original(dataset,key)
        with patch.object(converter,"MAX_CONNECTIVITY",5),patch.object(h5py.Dataset,"__getitem__",tracked):
            with self.assertRaisesRegex(converter.ConversionIssue,"allocation"):self.read()
        self.assertNotIn("/meshes/1/faces/nodes/8/nodes",read_names)

    def test_external_hdf5_link_and_adapted_cells_reject(self):
        with h5py.File(self.path,"r+") as handle:handle["forbidden"]=h5py.ExternalLink("not-opened.h5","/payload")
        with self.assertRaisesRegex(ValueError,"links"):self.read()
        self.create()
        with h5py.File(self.path,"r+") as handle:handle["meshes/1/cells/zoneTopology/childZoneId"][:]=[7]
        with self.assertRaisesRegex(ValueError,"adapted"):self.read()

    def test_nonintegral_offset_and_cell_type_reject(self):
        for path,key in (("meshes/1","nodeOffset"),("meshes/1/cells/ctype/1","elementType")):
            self.create()
            with h5py.File(self.path,"r+") as handle:handle[path].attrs[key]=np.asarray([0.],dtype="f8")
            with self.assertRaisesRegex(ValueError,"native|Native"):self.read()


if __name__=="__main__":unittest.main()
