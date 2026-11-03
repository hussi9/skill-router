"""Test stub for the MCP server subprocess: every urlopen is recorded and refused,
or (MCP_STUB_JEV=1) answered with a canned Jev reply choosing each question's first option."""
import io
import json
import os
import urllib.error
import urllib.request

_LOG = os.environ.get("MCP_STUB_NET_LOG")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(req, *a, **k):
    if _LOG:
        with open(_LOG, "a") as f:
            f.write(getattr(req, "full_url", str(req)) + "\n")
    if os.environ.get("MCP_STUB_JEV") == "1":
        body = json.loads(req.data.decode())
        answers = {qid: {"choice": next(iter(q.get("criteria") or {"none": 0})), "confidence": 0.9}
                   for qid, q in body["questions"].items()}
        return _Resp(json.dumps({"answers": answers, "usage": {"input_tokens": 1}}).encode())
    raise urllib.error.URLError("stubbed")


urllib.request.urlopen = _fake
