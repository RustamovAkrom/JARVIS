from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


REPLACEMENT = '''def _model_paths():
    """Ensure both ONNX models exist without sharing temporary files.

    Each process/thread downloads to its own temporary file. This matters on
    Windows: the previous fixed ``<model>.download`` path let two concurrent
    plugin actions open, truncate, replace, or unlink the same file, causing
    WinError 32. The final model is published with an atomic replace.
    """
    import tempfile

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_DATA_DIR, 0o700)
    except OSError:
        pass

    result = {}
    for filename, (url, minimum_size) in _MODELS.items():
        path = _DATA_DIR / filename

        try:
            if path.is_file() and path.stat().st_size >= minimum_size:
                result[filename] = path
                continue
        except OSError:
            # A concurrent process may be publishing the model. Recheck below
            # after our private download rather than trusting a transient stat.
            pass

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{filename}.{os.getpid()}.",
            suffix=".download",
            dir=str(_DATA_DIR),
        )
        temporary = Path(temporary_name)
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "JARVIS-Face-Activation/1.0"},
            )
            size = 0
            with os.fdopen(fd, "wb") as output:
                with urllib.request.urlopen(request, timeout=60) as response:
                    while True:
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        size += len(block)
                        if size > 80_000_000:
                            raise RuntimeError("Face model download exceeded the size limit.")
                        output.write(block)
                output.flush()
                os.fsync(output.fileno())

            if size < minimum_size:
                raise RuntimeError(
                    f"Downloaded face model {filename} is incomplete "
                    f"({size} bytes; expected at least {minimum_size})."
                )

            # Another JARVIS process may have completed the same download while
            # this one was reading. Keep its already-valid final file.
            try:
                if path.is_file() and path.stat().st_size >= minimum_size:
                    result[filename] = path
                    continue
            except OSError:
                pass

            try:
                temporary.replace(path)
            except PermissionError:
                # If another process won the race and published a valid model,
                # a Windows sharing violation on the replace is harmless.
                try:
                    if path.is_file() and path.stat().st_size >= minimum_size:
                        result[filename] = path
                        continue
                except OSError:
                    pass
                raise

            result[filename] = path
        finally:
            # tempfile.mkstemp guarantees this path belongs to this invocation;
            # never delete a shared <model>.download file owned by another call.
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                # Antivirus/indexing can briefly hold a just-closed download.
                # Retry briefly; do not mask the original download/replace error.
                for _ in range(5):
                    time.sleep(0.1)
                    try:
                        temporary.unlink(missing_ok=True)
                        break
                    except PermissionError:
                        continue

    return result
'''


def main() -> int:
    package_dir = Path(__file__).resolve().parent
    candidates = (package_dir, package_dir.parent)
    root = next((item for item in candidates if (item / "plugins" / "face_activation.py").is_file()), None)
    if root is None:
        print(
            "ERROR: plugins/face_activation.py was not found. "
            "Run this script from the extracted package inside/next to the JARVIS project.",
            file=sys.stderr,
        )
        return 2
    target = root / "plugins" / "face_activation.py"

    source = target.read_text(encoding="utf-8")
    start_marker = "def _model_paths():"
    end_marker = "\ndef _engines("
    start = source.find(start_marker)
    if start < 0:
        print("ERROR: cannot find def _model_paths() in plugins/face_activation.py", file=sys.stderr)
        return 3
    end = source.find(end_marker, start)
    if end < 0:
        print("ERROR: cannot find the _engines() boundary after _model_paths()", file=sys.stderr)
        return 4

    current = source[start:end].rstrip()
    if "tempfile.mkstemp" in current and "private temporary" in current:
        print("Already patched: plugins/face_activation.py")
        return 0

    backup = target.with_name(
        f"face_activation.py.bak.{datetime.now():%Y%m%d_%H%M%S}"
    )
    shutil.copy2(target, backup)
    updated = source[:start] + REPLACEMENT.rstrip() + source[end:]

    # Parse before touching the project file.
    compile(updated, str(target), "exec")
    target.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Patched: {target}")
    print(f"Backup:  {backup}")
    print("The only changed function is _model_paths().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
