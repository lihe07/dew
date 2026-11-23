"""Runs a script's process inside WebAssembly: CPython for WASI, which can't see files, the network, other processes, or the environment.

Started by the runner as `python -I wasm_host.py FUEL MEMORY`. The guest's stdin and stdout are this process's, so the JSON-line protocol of
sandbox_child.py passes straight through. Exit codes say how the guest ended: its own code, FUEL when it ran out of computation, TRAP for anything else.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("STARDEW_WASI", Path(__file__).resolve().parents[2] / ".tools/python-wasi"))
WASM = ROOT / "x/python.wasm"
LIB = ROOT / "x/lib"
CHILD = Path(__file__).with_name("sandbox_child.py")
FUEL, TRAP = 75, 70


def missing() -> str | None:
    """Why scripts can't run here, if they can't."""
    try:
        importlib.metadata.version("wasmtime")
    except importlib.metadata.PackageNotFoundError:
        return "the wasmtime package isn't installed (run `uv sync` in agent/)"
    if not WASM.is_file() or not LIB.is_dir():
        return f"CPython for WASI isn't at {ROOT / 'x'} (run `just setup-wasm`)"
    return None


def module(engine: object) -> object:
    """The compiled module: compiling takes seconds, so it's kept next to the wasm, keyed by the wasm's hash and wasmtime's version."""
    import wasmtime

    digest = hashlib.sha256(WASM.read_bytes()).hexdigest()[:16]
    cache = ROOT / f"python-{digest}-wasmtime{importlib.metadata.version('wasmtime')}.cwasm"
    if cache.is_file():
        try:
            return wasmtime.Module.deserialize_file(engine, str(cache))
        except wasmtime.WasmtimeError:
            cache.unlink()
    compiled = wasmtime.Module.from_file(engine, str(WASM))
    partial = cache.with_suffix(f".{os.getpid()}.tmp")
    partial.write_bytes(compiled.serialize())
    partial.replace(cache)
    return compiled


def engine() -> object:
    import wasmtime

    config = wasmtime.Config()
    config.consume_fuel = True
    return wasmtime.Engine(config)


def main() -> None:
    import wasmtime

    fuel, memory = int(sys.argv[1]), int(sys.argv[2])
    e = engine()
    compiled = module(e)
    store = wasmtime.Store(e)
    store.set_fuel(fuel)
    store.set_limits(memory_size=memory)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-I", "-c", CHILD.read_text()]
    wasi.inherit_stdin()
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    wasi.preopen_dir(str(LIB), "/lib", fs_mutable=False)
    wasi.env = [("PYTHONHOME", "/")]
    store.set_wasi(wasi)
    linker = wasmtime.Linker(e)
    linker.define_wasi()
    try:
        linker.instantiate(store, compiled).exports(store)["_start"](store)
    except wasmtime.ExitTrap as ex:
        sys.exit(ex.code)
    except wasmtime.Trap as ex:
        text = str(ex)
        if "fuel" in text:
            sys.exit(FUEL)
        print(f"TRAP: {text.splitlines()[0]}", file=sys.stderr)
        sys.exit(TRAP)


if __name__ == "__main__":
    if sys.argv[1:] == ["--compile"]:
        module(engine())
        print("compiled")
    else:
        main()
