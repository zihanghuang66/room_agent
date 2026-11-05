"""Codex-only classification in the Room fork."""
import os
DEFAULT_CLASSIFY_MODEL='codex'
def provider():
    selected=os.environ.get('LR_CLASSIFY_PROVIDER','codex').strip().lower()
    if selected!='codex':raise ValueError('This Room profile requires Codex classification')
    return selected
def classify_image(*args,**kwargs):
    provider()
    from .classify_codex import classify_image as classify
    return classify(*args,**kwargs)
