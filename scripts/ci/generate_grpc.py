"""Regenerate the gRPC Python code from proto/ (needs grpcio-tools); --check fails when the committed code is stale.

The generated modules are committed so the service needs only the grpcio and protobuf runtimes. Run this after
changing a .proto file: python scripts/ci/generate_grpc.py
"""

import argparse
import filecmp
import sys
import tempfile
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parents[2]
PROTO_ROOT = ROOT / 'proto'
OUTPUT_ROOT = ROOT / 'src'
GENERATED_SUFFIXES = ('_pb2.py', '_pb2.pyi', '_pb2_grpc.py')


def generate(output: Path) -> list[Path]:
    """Compile every .proto under proto/ into ``output``; returns the generated files."""
    protos = sorted(PROTO_ROOT.rglob('*.proto'))
    include = str(Path(protoc.__file__).parent / '_proto')
    arguments = [
        'protoc',
        f'-I{PROTO_ROOT}',
        f'-I{include}',
        f'--python_out={output}',
        f'--pyi_out={output}',
        f'--grpc_python_out={output}',
        *map(str, protos),
    ]
    if protoc.main(arguments) != 0:
        raise SystemExit('protoc failed')
    return sorted(path for path in output.rglob('*') if path.name.endswith(GENERATED_SUFFIXES))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail if the committed code differs from a fresh build')
    args = parser.parse_args()
    if not args.check:
        for path in generate(OUTPUT_ROOT):
            print(path.relative_to(ROOT))
        return 0
    with tempfile.TemporaryDirectory() as scratch:
        stale = [
            str(fresh.relative_to(scratch))
            for fresh in generate(Path(scratch))
            if not (OUTPUT_ROOT / fresh.relative_to(scratch)).is_file()
            or not filecmp.cmp(fresh, OUTPUT_ROOT / fresh.relative_to(scratch), shallow=False)
        ]
    if stale:
        print('Stale generated gRPC code (run scripts/ci/generate_grpc.py):', *stale, sep='\n  ', file=sys.stderr)
        return 1
    print('Generated gRPC code is up to date')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
