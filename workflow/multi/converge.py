import os
import sys
from pathlib import Path
import json
sys.path.append(
    str(Path.joinpath(Path(__file__).parent, "../../").resolve()))  # noqa
from jobber import Submit  # noqa
from qeio.env import Env  # noqa
from qeio.create import PW  # noqa

# predefinition
with open(Path.joinpath(Path(__file__).parent, "../../settings/variables.json").resolve(), "r") as f:
    variables = json.load(f)
input_file = sys.argv[1]
outpath = str((Path(input_file).parent).resolve())
env = Env.from_cif(input_file, variables)
job = []
# workflow
# cutoff
ecutwfc = env.ecutwfc
ecutrho = env.ecutrho
ecut_loop = {}
for i, n in enumerate([0.8, 1, 1.2, 1.4]):
    outpath_i = outpath + f"/ecut{i}"
    os.makedirs(outpath_i)
    env.ecutwfc = ecutwfc * n
    env.ecutrho = ecutrho * n
    ecut_loop.add(PW(outpath_i, env, variables, calculation="scf")]
# TODO : select cutoff
# env.ecutwfc =
# env.ecutrho =
# k mesh
for k in [4, 8, 12, 16]:
    outpath_k = outpath + f"/nk{k}"
    os.makedirs(outpath_k)
    variables["nk"] = [k, k, k]
    submit = Submit(
        [PW(outpath_k, env, variables, calculation="scf")], outpath_k)
    submit()
job = [ecut_loop, k_loop]
