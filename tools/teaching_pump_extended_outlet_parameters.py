"""Write only the exact declared geometry input before native CAD generation."""
import argparse
import os
from pathlib import Path
import teaching_pump_geometry as geometry
import teaching_pump_extended_outlet_recipe as recipe

def write(output):
    recipe.verify_profile(recipe.PROFILE)
    value=recipe.verify_geometry_parameters(recipe.EXPECTED_GEOMETRY_PARAMETERS)
    target=Path(output).absolute()
    geometry.context_guard()
    root=Path(os.environ["RUNNER_TEMP"]).absolute()
    recipe.require(target.parent==root and not target.exists(),"Write only the fresh exact runner temporary parameter file.")
    for item in (target,*target.parents):
        recipe.require(not item.is_symlink() and not getattr(item,"is_junction",lambda:False)(),"Parameter paths cannot contain links or junctions.")
    with target.open("xb") as stream:stream.write(recipe.encoded(value))
    return value

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",required=True)
    args=parser.parse_args();write(args.output)
