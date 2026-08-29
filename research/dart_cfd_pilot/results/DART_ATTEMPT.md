# DART execution attempt

Recorded against DART commit `b4f954319ad4c26ab1372d130719eb2f4ddd4ea6`.

The repository cloned and installed in a clean Python 3.12 environment with
PyTorch 2.7.1 CPU. Import initially failed because `triton` is imported on
Linux but is not declared as a Linux dependency in `pyproject.toml`. After
installing `triton==3.3.1`, `demo_multiclass.py --help` completed.

An attempted CPU inference then encountered CUDA-only tensor allocations in
the positional-encoding and decoder constructors. Temporary local diagnostic
edits changed those two allocations to use CPU when CUDA was unavailable. The
next gate was the expected authoritative blocker:

```text
huggingface_hub.errors.GatedRepoError: 401 Client Error
Cannot access gated repo ... facebook/sam3 ... authenticated access required.
```

No CFD detection or segmentation was produced. This is a preflight result,
not a DART accuracy result. The temporary edits were used only to diagnose the
next failure and are not vendored here as a fork of DART.
