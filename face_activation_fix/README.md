# JARVIS Face Activation: Windows WinError 32 fix

The error shown in the Plugin Settings dialog names `sface.onnx.download`.
The previous `_model_paths()` implementation used one fixed temporary filename
for every invocation. Two simultaneous calls (for example, enrolment and face
verification, or two JARVIS processes) could therefore write/delete the same
file. Windows reports this as `WinError 32` when one invocation touches a file
that another invocation still has open.

## Apply

1. Extract the ZIP into the JARVIS project root (the directory containing
   `plugins/` and `core/`). It creates a `face_activation_fix/` folder.
2. Run from the project root:

   ```powershell
   python .\face_activation_fix\apply_face_activation_fix.py
   ```

The script backs up `plugins/face_activation.py`, replaces only `_model_paths()`
and compiles the modified source before writing it. It does not modify other
plugins, `main.py`, `ui.py`, camera code, settings, or stored face data.

## Verify

```powershell
python -m unittest face_activation_fix.tests.test_face_activation_download -v
python -m compileall -q plugins/face_activation.py
```

The test uses two concurrent threads and a mocked download. It does not access
the webcam or download the real ONNX files. After it passes, restart JARVIS and
try **APPLY FACE DATA ACTION → Enroll or update** again.

If the same error persists, close all JARVIS processes before retrying. A
remaining process or antivirus scanner may be holding the temporary model file.
